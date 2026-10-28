"""update_flow uploads the flow JSON without touching the filesystem."""

import json
from unittest import mock

from django.test import SimpleTestCase

from horilla.testkit import read_only_cwd
from whatsapp.utils import update_flow


class UpdateFlowTests(SimpleTestCase):
    @mock.patch("whatsapp.utils.requests.post")
    def test_flow_json_is_sent_from_memory(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {"success": True}
        flow = {"version": "6.0", "screens": []}

        with read_only_cwd():
            update_flow("123", flow, "token")

        name, payload, content_type = post.call_args.kwargs["files"]["file"]
        self.assertEqual(name, "flow.json")
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(payload), flow)
