
import time
import dns.resolver

class TxtRecord:
    def __init__(self, txt):
        for entry in txt.split(' '):
            parts = entry.split('=')
            if len(parts) != 2:
                continue
            key, value = parts
            if key == 'p':
                self.priority = value
        

class Failover:
    def __init__(self, log, failover_record, failover_instance_id, failover_priority):
        self.log = log
        self.failover_record = failover_record
        self.failover_instance_id = failover_instance_id
        self.failover_priority = failover_priority

    def this_is_active(self):
        try:
            dns_response = dns.resolver.query(self.failover_record, 'TXT')
        except dns.resolver.NXDOMAIN:
            self.log.info("No failover DNS record (NXDOMAIN), assuming this is the first run")
            return True
        except dns.resolver.DNSException as ex:
            self.log.error("DNS lookup for failover record failed: %s", ex)
            return False
            
        txt_records = [txt.to_text() for txt in self._dns_response]
        parsed_records = []        
        for txt in txt_records:
            try:
                parsed = TxtRecord(txt)
                parsed_records.append(parsed)
            except ValueError as ex:
                self.log.error("Failed to parse failover record: %s: %r", ex, txt)
                continue

        if not parsed_records:
            self.log.error("No failover DNS records, assuming this is the first run")
            return True

        return True

    def update_record(self, *, update_callback):
        ts = int(time.time())
        txt_value = f"iid={self.failover_instance_id} p={self.failover_priority} t={ts}"
        
        update_callback(self.failover_record, txt_value)


