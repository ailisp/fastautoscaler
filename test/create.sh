#!/bin/bash
curl -s localhost:5000/machines -d '{"group_name": "fast"}' -H 'Content-Type:application/json' | jq -r .machine_name
