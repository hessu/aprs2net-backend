
import CloudFlare

CF_COMMENT = "aprs2-dynamic"

class aprs2cf:
    def __init__(self, log, token, zones):
        self.log = log
        self.zones = zones
        
        self.log.info("Cloudflare init, token: %s", token)
        self.cf = CloudFlare.CloudFlare(token=token)
        
        self.zone_ids = {
            'aprs.is': 'f49d2d125fdb4eb470041a8d218966ba',
            'aprs2.net': 'd7c9ec1d772da32eefdb89f962788913',
        }
        
    def dns_push(self, logid, zone, fqdn, v4_addrs = [], v6_addrs = [], cname = None):
        use_zone = zone
        zone_id = self.zone_ids[use_zone]
        
        trim_end = '.' + zone
        if not fqdn.endswith(trim_end):
            self.log.error("FQDN %s does not end with zone %s", fqdn, trim_end)
        name = fqdn[0:len(trim_end)*-1]
        self.log.info("Updating %r in zone %r", name, use_zone)

        if cname and (v4_addrs or v6_addrs):
            raise ValueError("CNAME and other data")

        fqdn_query = name + "." + use_zone
        old_records = []
        try:
            old_records = self.cf.zones.dns_records.get(zone_id, params={"name": fqdn_query})
        except CloudFlare.exceptions.CloudFlareAPIError as e:
            self.log.error("Failed to fetch existing CF DNS record %s: %d %s", name, e, e)    
            return

        self.log.info("Existing record: %r", old_records)
        existing_a = {}
        existing_aaaa = {}
        existing_cname = {}
        for r in old_records:
            type = r.get('type')
            if type == 'A':
                existing_a[r["content"]] = r["id"]
            elif type == 'AAAA':
                existing_aaaa[r["content"]] = r["id"]
            elif type == 'CNAME':
                existing_cname[r["content"]] = r["id"]

        records_required = []
        
        for v4_addr in v4_addrs:
            if v4_addr not in existing_a:
                records_required.append({'name': name, 'type':'A', 'content': v4_addr, 'comment': CF_COMMENT })
                self.log.info("%s should add A %s", fqdn_query, v4_addr)
            else:
                self.log.info("%s already has A %s", fqdn_query, v4_addr)
        for v6_addr in v6_addrs:
            if v6_addr not in existing_aaaa:
                records_required.append({'name': name, 'type':'AAAA', 'content': v6_addr, 'comment': CF_COMMENT})
                self.log.info("%s should add AAAA %s", fqdn_query, v6_addr)
            else:
                self.log.info("%s already has AAAA %s", fqdn_query, v6_addr)
        if cname:
            if cname not in existing_cname:
                records_required = [{'name': name, 'type':'CNAME', 'content': cname, 'comment': CF_COMMENT}]
                self.log.info("%s should add CNAME %s", fqdn_query, cname)
            else:
                self.log.info("%s already has CNAME %s", fqdn_query, cname)

        ids_to_delete = []
        if not cname and existing_cname:
            ids_to_delete.extend(existing_cname.values())

        for a in existing_a:
            if a not in v4_addrs:
                self.log.info("%s has %s - should delete", fqdn_query, a)
                ids_to_delete.append(existing_a[a])
                
        for aaaa in existing_aaaa:
            if aaaa not in v6_addrs:
                self.log.info("%s has %s - should delete", fqdn_query, aaaa)
                ids_to_delete.append(existing_aaaa[aaaa])

        if cname and records_required:
            # delete all but one record and replace one with the CNAME:
            record_required = records_required[0]
            if len(old_records) > 1:
                for rec in old_records[1:]:
                    try:
                        r = self.cf.zones.dns_records.delete(zone_id, rec['id'])
                    except CloudFlare.exceptions.CloudFlareAPIError as e:
                        self.log.error("Failed to delete CF DNS record %s %s %s: %d %s", fqdn_query, rec['type'], rec['content'], e, e)
            if old_records:
                self.log.info("%s: replacing id %s with CNAME %r", fqdn_query, old_records[0]['id'], record_required)
                try:
                    r = self.cf.zones.dns_records.put(zone_id, old_records[0]['id'], data=record_required)
                except CloudFlare.exceptions.CloudFlareAPIError as e:
                    self.log.error("Failed to replace record CF DNS %s with CNAME %s: %d %s", fqdn_query, cname, e, e)
            else:
                self.log.info("%s: inserting CNAME %r", fqdn_query, record_required)
                try:
                    r = self.cf.zones.dns_records.post(zone_id, data=record_required)
                except CloudFlare.exceptions.CloudFlareAPIError as e:
                    self.log.error("Failed to create record CF DNS %s CNAME %s: %d %s", fqdn_query, cname, e, e)
            return

        if ids_to_delete:
            self.log.info("%s records to delete: %r", fqdn_query, ids_to_delete)

        for record in records_required:
            if ids_to_delete:
                # replace one of the old records
                id_to_replace = ids_to_delete.pop(0)
                self.log.info("%s: replacing id %s with %r", fqdn_query, id_to_replace, record)
                try:
                    r = self.cf.zones.dns_records.put(zone_id, id_to_replace, data=record)
                except CloudFlare.exceptions.CloudFlareAPIError as e:
                    self.log.error("Failed to replace record CF DNS %s: %d %s", fqdn_query, e, e)
            else:
                self.log.info("%s: inserting %r", fqdn_query, record)
                try:
                    r = self.cf.zones.dns_records.post(zone_id, data=record)
                except CloudFlare.exceptions.CloudFlareAPIError as e:
                    self.log.error("Failed to create record CF DNS %s: %d %s", fqdn_query, e, e)
        
        for record_id in ids_to_delete:
            self.log.info("%s: deleting %r", fqdn_query, record_id)
            try:
                r = self.cf.zones.dns_records.delete(zone_id, record_id)
            except CloudFlare.exceptions.CloudFlareAPIError as e:
                self.log.error("Failed to delete CF DNS record %s %s: %d %s", fqdn_query, record_id, e, e)
                
        #zones = cf.zones.get(params={'per_page':50})

