"""Verify that Samsung panel actions stay within the local Mom dashboard."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mom_dashboard_panel", ROOT / "scripts/mom-dashboard-panel.py")
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


class MomPanelRemoteTests(unittest.TestCase):
    def test_mom_image_provisions_only_the_remote_variant_without_relaxing_env_security(self):
        provisioning = (ROOT / "scripts/apply-system-config.sh").read_text()
        unit = (ROOT / "systemd/tv-kiosk-remote-mom.conf").read_text()
        self.assertEqual(unit, "[Service]\nEnvironment=KIOSK_VARIANT=mom\n")
        begin = provisioning.index('REMOTE_UNIT="/home/$KIOSK_USER/.config/systemd/user/tv-kiosk-remote.service"')
        end = provisioning.index('AUDIO_UNIT=', begin)
        scope = provisioning[begin:end]
        self.assertIn('mom:*"Raspberry Pi 4"*|mom:*"Raspberry Pi 5"*', scope)
        self.assertIn('"$REMOTE_DROPIN/50-mom-panel.conf"', scope)
        self.assertNotIn('chmod', scope)

    @staticmethod
    def target(port, url=panel.MOM_PANEL_ORIGIN + "/", id="testBaiamonte"):
        return {"type": "page", "url": url, "id": id, "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/{id}"}

    def test_libcec_mapped_input_dispatch_uses_exact_labels_and_only_mom(self):
        launcher = (ROOT / "scripts" / "rahamin-kiosk-remote").read_text()
        funcs = launcher.split('echo "Rahamin Kiosk Samsung remote listener starting"', 1)[0]
        shell = funcs + """
timeout() { printf 'panel-action %s\\n' "$4"; }
wtype() { printf 'existing-keys %s\\n' "$*"; }
for fixture in   'key pressed: exit (d) current(0) duration(0)'   'key pressed: return (d)'   'key pressed: back (d)'   'key pressed: root menu (9)'   'key pressed: display information (35)'   'key pressed: backward (4c)'   'key pressed: up (1)'   'key released: exit (d) D:100ms'   'key pressed: backward special (00)'; do
    action="$(cec_action "$fixture")"
    [ -n "$action" ] && send_action "$action"
done
KIOSK_VARIANT=auto
action="$(cec_action 'key pressed: exit (d)')"
[ -n "$action" ] && send_action "$action"
send_action close_panel
"""
        result = subprocess.run(["/bin/sh", "-c", "KIOSK_VARIANT=mom\n" + shell], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), [
            "panel-action close", "panel-action close", "panel-action close",
            "panel-action toggle", "panel-action toggle",
            "existing-keys -M ctrl -P minus -p minus -m ctrl",
            "existing-keys -P up -p up",
            "existing-keys -M ctrl -P minus -p minus -m ctrl",
        ])

    def test_only_matching_target_local_socket_is_used_in_either_layout(self):
        for designated in (panel.DEBUG_PORT, panel.DEBUG_PORT + 1):
            with self.subTest(designated=designated):
                def targets(_path, *, timeout, port):
                    decoy = self.target(port, url="http://127.0.0.1:18998/tv", id="Miami")
                    return [decoy, self.target(port)] if port == designated else [decoy]
                with (
                    patch.object(panel, "KIOSK_VARIANT", "mom"),
                    patch.object(panel, "devtools", side_effect=targets),
                    patch.object(panel, "websocket_command", return_value={"result": {"value": True}}) as command,
                ):
                    self.assertTrue(panel.mom_panel_action("close"))
                args, _ = command.call_args
                self.assertEqual(args[0], self.target(designated)["webSocketDebuggerUrl"])
                self.assertEqual(args[1], "Runtime.evaluate")
                self.assertIn("controlClose", args[2]["expression"])
                self.assertIn("panel.hidden", args[2]["expression"])

    def test_no_action_in_other_profiles_or_with_ambiguous_or_untrusted_targets(self):
        port = panel.DEBUG_PORT
        bad = self.target(port)
        bad["webSocketDebuggerUrl"] = bad["webSocketDebuggerUrl"].replace("127.0.0.1", "203.0.113.8")
        wrong_port = self.target(port + 9)
        malformed = self.target(port)
        malformed["webSocketDebuggerUrl"] = malformed["webSocketDebuggerUrl"].replace(str(port), "abc")
        for variant, action, targets in (
            ("rahamin", "toggle", [[self.target(port)], []]),
            ("mom", "bad-action", [[self.target(port)], []]),
            ("mom", "toggle", [[bad, wrong_port, malformed], []]),
            ("mom", "toggle", [[self.target(port)], [self.target(port + 1)]]),
            ("mom", "toggle", [[self.target(port, "https://tenutabaiamonte.example/")], []]),
        ):
            with self.subTest(variant=variant, action=action, targets=targets):
                with (
                    patch.object(panel, "KIOSK_VARIANT", variant),
                    patch.object(panel, "devtools", side_effect=targets),
                    patch.object(panel, "websocket_command") as command,
                ):
                    self.assertFalse(panel.mom_panel_action(action))
                command.assert_not_called()

    @unittest.skipUnless(shutil.which("node"), "Node is optional for direct DOM-script evaluation")
    def test_evaluates_fixed_existing_actions_and_blocks_stale_document(self):
        for action, origin, title, visible, initially_hidden, allowed in (
            ("toggle", panel.MOM_PANEL_ORIGIN, "Tenuta Baiamonte Live", True, True, True),
            ("toggle", panel.MOM_PANEL_ORIGIN, "Tenuta Baiamonte Live", True, False, True),
            ("close", panel.MOM_PANEL_ORIGIN, "Tenuta Baiamonte Live", True, False, True),
            ("close", panel.MOM_PANEL_ORIGIN, "Tenuta Baiamonte Live", True, True, False),
            ("toggle", "http://127.0.0.1:18998", "Tenuta Baiamonte Live", True, False, False),
            ("toggle", panel.MOM_PANEL_ORIGIN, "Cloudflare Access", True, False, False),
            ("toggle", panel.MOM_PANEL_ORIGIN, "Tenuta Baiamonte Live", False, True, False),
        ):
            with self.subTest(action=action, origin=origin, title=title, visible=visible, initially_hidden=initially_hidden):
                port = panel.DEBUG_PORT
                def command(_url, _method, params, *, timeout):
                    expected_button = "controlToggle" if action == "toggle" else "controlClose"
                    state = {
                        "location": {"origin": origin, "pathname": "/"},
                        "title": title, "visibility": "visible" if visible else "hidden",
                        "hidden": initially_hidden, "button": expected_button,
                    }
                    js = """
const vm = require('node:vm');
const cfg = STATE;
const localPanel = {hidden: cfg.hidden};
let clicks = 0;
const document = {title: cfg.title, visibilityState: cfg.visibility,
  getElementById(id) {
    if (id === 'tvControlPanel') return localPanel;
    if (id === cfg.button) return {click() { clicks++; }};
    return null;
  }};
const result = vm.runInNewContext(EXPRESSION, {location: cfg.location, document});
console.log(JSON.stringify({result, clicks}));
""".replace("STATE", json.dumps(state)).replace("EXPRESSION", json.dumps(params["expression"]))
                    res = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
                    answer = json.loads(res.stdout)
                    self.assertEqual(answer["clicks"], int(allowed))
                    return {"result": {"value": answer["result"]}}
                with (
                    patch.object(panel, "KIOSK_VARIANT", "mom"),
                    patch.object(panel, "devtools", side_effect=[[self.target(port)], []]),
                    patch.object(panel, "websocket_command", side_effect=command),
                ):
                    self.assertEqual(panel.mom_panel_action(action), allowed)
