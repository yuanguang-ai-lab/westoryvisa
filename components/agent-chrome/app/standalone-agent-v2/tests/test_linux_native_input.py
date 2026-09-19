import subprocess
import unittest

from visa_agent_v2.native_input import (
    LinuxX11NativeInput,
    NativeInputUnavailable,
)


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.cursor = [20, 30]

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        args = list(command[1:])
        if args[:2] == ["getmouselocation", "--shell"]:
            output = (
                f"X={self.cursor[0]}\nY={self.cursor[1]}\n"
                "SCREEN=0\nWINDOW=1\n"
            )
        elif args[:1] == ["mousemove"]:
            self.cursor = [int(args[-2]), int(args[-1])]
            output = ""
        elif args[:3] == ["search", "--onlyvisible", "--name"]:
            output = "73400325\n"
        elif args[:2] == ["getwindowgeometry", "--shell"]:
            output = "X=0\nY=0\nWIDTH=1440\nHEIGHT=900\n"
        else:
            output = ""
        return subprocess.CompletedProcess(command, 0, output, "")


class LinuxNativeInputTests(unittest.TestCase):
    def build_backend(self):
        runner = FakeRunner()
        backend = LinuxX11NativeInput(
            environ={"DISPLAY": ":101"},
            runner=runner,
            which=lambda name: f"/usr/bin/{name}",
        )
        return backend, runner

    def test_missing_display_fails_closed(self):
        with self.assertRaises(NativeInputUnavailable):
            LinuxX11NativeInput(
                environ={},
                runner=FakeRunner(),
                which=lambda name: f"/usr/bin/{name}",
            )

    def test_activates_matching_chromium_window(self):
        backend, runner = self.build_backend()

        origin = backend.activate_browser_window(
            "DocFlow Native Select Fixture",
            window_bounds={
                "left": 0,
                "top": 0,
                "width": 1440,
                "height": 900,
            },
        )

        self.assertIsNone(origin)
        commands = [call[0] for call in runner.calls]
        self.assertIn(
            ["/usr/bin/xdotool", "windowactivate", "--sync", "73400325"],
            commands,
        )

    def test_select_option_uses_xtest_keys_not_dom_assignment(self):
        backend, runner = self.build_backend()

        backend.select_option(140, 180, "BUSINESS", option_steps=2)

        commands = [call[0][1:] for call in runner.calls]
        self.assertTrue(backend.focus_select_before_input)
        self.assertIn(
            ["key", "--clearmodifiers", "alt+Down"],
            commands,
        )
        self.assertIn(["key", "--clearmodifiers", "Home"], commands)
        self.assertEqual(
            commands.count(["key", "--clearmodifiers", "Down"]),
            2,
        )
        self.assertIn(["key", "--clearmodifiers", "Return"], commands)
        self.assertFalse(any(
            command[:1] in (["mousemove"], ["mousedown"], ["mouseup"])
            for command in commands
        ))

    def test_move_to_current_point_does_not_wait_for_motion_event(self):
        backend, runner = self.build_backend()

        backend._move_mouse_visibly(20, 30)

        commands = [call[0][1:] for call in runner.calls]
        self.assertFalse(any(command[:1] == ["mousemove"] for command in commands))


if __name__ == "__main__":
    unittest.main()
