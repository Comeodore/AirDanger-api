import base64
import json
import os
import zoneinfo

import kyiv_tzif

TZ_DIR = "/tmp/zoneinfo"
os.makedirs(f"{TZ_DIR}/Europe", exist_ok=True)
with open(f"{TZ_DIR}/Europe/Kyiv", "wb") as tz_file:
    tz_file.write(base64.b64decode(kyiv_tzif.DATA))
zoneinfo.reset_tzpath(to=[TZ_DIR])

from workers import Response, WorkerEntrypoint

from app.brain import commit_request, process_request
from app.danger_service import DangerService

DANGER = DangerService()


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("not found", status=404)

    async def process(self, request):
        return json.dumps(process_request(DANGER, json.loads(request)), ensure_ascii=False)

    async def commit(self, request):
        return json.dumps(commit_request(DANGER, json.loads(request)))
