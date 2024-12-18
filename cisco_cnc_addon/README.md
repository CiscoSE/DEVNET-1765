# cisco_cnc_addon


## Example Topology Query for Missile Map
```
index="cdc_events" sourcetype="crosswork-topology-edge" | dedup uuid | join host [search index="cdc_events" sourcetype="crosswork-inventory-node" | dedup host | fields + host, geo_info.coordinates.latitude.value, geo_info.coordinates.longitude.value | rename geo_info.coordinates.latitude.value as start_lat, geo_info.coordinates.longitude.value as start_lon] | join target [search index="cdc_events" sourcetype="crosswork-inventory-node" | dedup host | fields + host, geo_info.coordinates.latitude.value, geo_info.coordinates.longitude.value | rename host as target, geo_info.coordinates.latitude.value as end_lat, geo_info.coordinates.longitude.value as end_lon] | fields + host, target, start_lat, start_lon, end_lat, end_lon, attributes.decoration.color | rename host as start_label, target as end_label,  attributes.decoration.color as color | search start_lat=* start_lon=* end_lat=* end_lon=*
```