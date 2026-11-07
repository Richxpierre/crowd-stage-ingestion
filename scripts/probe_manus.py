"""Read-only Manus authentication and budget-readiness check. Creates no task."""
import json
from collector.manus_backup import ManusClient

def main():
    client=ManusClient()
    try:print(json.dumps(client.authenticate()))
    finally:client.close()

if __name__=='__main__':main()

