
"""
DNS driver failover: a primary instance and a backup instance.

The primary instance always updates DNS. After each successful update round
it rewrites the failover TXT record with a value like

    iid=<instance id> t=<unix timestamp>

The timestamp makes the value change on every write, so the record works as
a heartbeat.

The backup instance never writes the TXT record. It resolves the record on
every cycle and watches for the value changing. If the value stays the same
for the timeout period (15 minutes by default), the primary is assumed to be
dead and the backup starts updating the rotates. As soon as the value changes
again, the primary is back and the backup goes passive. The backup does not
touch the TXT record while it is covering for the primary, so the primary's
first write after recovery hands the job back.

The staleness is measured with the local monotonic clock from the moment a
value is first observed, so it does not depend on clock synchronisation
between the instances, and a freshly started backup always waits a full
timeout period before taking over.

The backup never takes over when the record is missing or unparseable, or
when the lookup fails: it keeps whatever state it was in. So the primary has
to have written the record at least once before the backup will ever cover
for it.

Note that the resolver used for the lookups may cache the record for its TTL,
so the TXT record should get a TTL which is short compared to the timeout.
"""

import time
import dns.exception
import dns.resolver

ROLE_PRIMARY = 'primary'
ROLE_BACKUP = 'backup'
ROLES = (ROLE_PRIMARY, ROLE_BACKUP)

# How long the record must stay unchanged before the backup takes over, seconds
DEFAULT_TIMEOUT = 15 * 60

# Resolver timeout for a single lookup, seconds
RESOLVE_LIFETIME = 10


class TxtRecord:
    """
    One parsed failover TXT record: space separated key=value pairs.
    """
    def __init__(self, txt):
        self.txt = txt
        self.instance_id = None
        self.timestamp = None

        for entry in txt.split():
            parts = entry.split('=', 1)
            if len(parts) != 2:
                continue
            key, value = parts
            if key == 'iid':
                self.instance_id = value
            elif key == 't':
                self.timestamp = int(value)

        if self.instance_id is None or self.timestamp is None:
            raise ValueError("missing iid or t")

    def __repr__(self):
        return "TxtRecord(%r)" % self.txt


class Failover:
    def __init__(self, log, failover_record, failover_instance_id, failover_role, timeout=DEFAULT_TIMEOUT):
        if failover_role not in ROLES:
            raise ValueError("failover role must be one of %s, not %r" % (', '.join(ROLES), failover_role))

        self.log = log
        self.failover_record = failover_record
        self.failover_instance_id = failover_instance_id
        self.role = failover_role
        self.timeout = timeout

        # The primary is always active. The backup starts passive and lets
        # the record decide.
        self.active = (self.role == ROLE_PRIMARY)

        # The last observed record value and the monotonic time it was
        # first seen, for staleness detection on the backup.
        self.seen_value = None
        self.seen_since = None

        self.log.info("Failover: this is the %s instance %s, record %s, timeout %d s",
            self.role, self.failover_instance_id, self.failover_record, self.timeout)

    def _resolve(self):
        """
        Resolve the failover TXT record. Returns a list of raw record
        strings, an empty list if the record does not exist, or None if the
        lookup failed.
        """
        try:
            answer = dns.resolver.resolve(self.failover_record, 'TXT', lifetime=RESOLVE_LIFETIME)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.exception.DNSException as ex:
            self.log.error("DNS lookup for failover record %s failed: %r", self.failover_record, ex)
            return None

        return [b''.join(rdata.strings).decode('ascii', 'replace') for rdata in answer]

    def _parse(self, txt_records):
        """
        Parse the raw record strings, dropping the ones which do not look
        like failover records.
        """
        parsed = []
        for txt in txt_records:
            try:
                parsed.append(TxtRecord(txt))
            except ValueError as ex:
                self.log.error("Failed to parse failover record: %s: %r", ex, txt)
        return parsed

    def _set_active(self, active, reason):
        if active != self.active:
            self.log.warning("Failover: becoming %s: %s", 'ACTIVE' if active else 'PASSIVE', reason)
        self.active = active
        return active

    def _observe(self, value):
        """
        Track changes to the record value. Returns the number of seconds the
        current value has remained unchanged.
        """
        now = time.monotonic()
        if value != self.seen_value:
            self.seen_value = value
            self.seen_since = now
        return now - self.seen_since

    def this_is_active(self):
        """
        Decide whether this instance should update DNS on this cycle.
        """
        if self.role == ROLE_PRIMARY:
            return True

        txt_records = self._resolve()
        if txt_records is None:
            # Lookup failed: nothing is known, keep the previous state
            self.log.error("Failover: lookup failed, staying %s", 'active' if self.active else 'passive')
            return self.active

        records = self._parse(txt_records)
        if not records:
            # No usable heartbeat: do not take over on that, keep the state
            self.log.error("Failover: no usable failover record at %s, staying %s",
                self.failover_record, 'active' if self.active else 'passive')
            return self.active

        # Watch the newest record, should there be several
        heartbeat = max(records, key=lambda r: r.timestamp)
        unchanged_for = self._observe(heartbeat.txt)

        if unchanged_for >= self.timeout:
            return self._set_active(True,
                "primary %s heartbeat unchanged for %d seconds, assuming it is dead and covering for it"
                % (heartbeat.instance_id, unchanged_for))

        if self.active:
            reason = "primary %s heartbeat is updating again" % heartbeat.instance_id
        else:
            reason = None
            self.log.debug("Failover: passive, primary %s heartbeat unchanged for %d of %d seconds",
                heartbeat.instance_id, unchanged_for, self.timeout)
        return self._set_active(False, reason)

    def update_record(self, *, update_callback):
        """
        Write the heartbeat record. The driver calls this after a successful
        DNS update round. Only the primary writes it; the backup never does,
        so that the primary's heartbeat is the only thing the backup watches.
        """
        if self.role != ROLE_PRIMARY:
            return

        ts = int(time.time())
        txt_value = "iid=%s t=%d" % (self.failover_instance_id, ts)

        update_callback(self.failover_record, txt_value)
