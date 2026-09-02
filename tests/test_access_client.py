import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACCESS_HTML = ROOT / "app" / "static" / "access.html"

HARNESS = r"""
const fs = require("node:fs");
const vm = require("node:vm");

const input = JSON.parse(fs.readFileSync(0, "utf8"));

function classList(initial = []) {
  const values = new Set(initial);
  return {
    add: (...names) => names.forEach((name) => values.add(name)),
    remove: (...names) => names.forEach((name) => values.delete(name)),
    replace: (oldName, newName) => {
      if (!values.delete(oldName)) return false;
      values.add(newName);
      return true;
    },
  };
}

let document;
function element(name, initialClasses = []) {
  return {
    name,
    classList: classList(initialClasses),
    listeners: {},
    textContent: "",
    value: "",
    open: false,
    disabled: false,
    focusCount: 0,
    addEventListener(type, listener) {
      this.listeners[type] = listener;
    },
    focus() {
      this.focusCount += 1;
      document.activeElement = this;
    },
    scrollIntoView() {},
  };
}

const form = element("form");
const password = element("password");
const status = element("status");
const owner = element("owner");
const guest = element("guest");
const guestButton = element("guest-button", ["ghost"]);
const guestStatus = element("guest-status");
const summary = element("summary");
const guestLede = element("guest-lede");
summary.textContent = "one of the room's humans?";
guestButton.textContent = "watch the room";
guestLede.textContent = "the room is awake. the window is open.";
owner.querySelector = (selector) => selector === "summary" ? summary : null;

const threshold = {
  children: [guest, owner],
  insertBefore(item, before) {
    this.children = this.children.filter((child) => child !== item);
    this.children.splice(this.children.indexOf(before), 0, item);
  },
};
owner.parentNode = threshold;

const ids = {
  "access-form": form,
  password,
  status,
  "owner-access": owner,
  "guest-button": guestButton,
  "guest-status": guestStatus,
};
const body = {
  dataset: {guestOpen: input.guestOpen ? "true" : "false"},
  classList: classList(),
};
document = {
  body,
  activeElement: null,
  getElementById: (id) => ids[id],
  querySelector: (selector) => {
    if (selector === ".access-guest") return guest;
    if (selector === ".access-guest-lede") return guestLede;
    return null;
  },
};

const assignments = [];
const location = {
  origin: "https://woolroom.test",
  search: input.search,
  assign: (value) => assignments.push(value),
};
const context = {
  document,
  location,
  URL,
  URLSearchParams,
  matchMedia: (query) => ({
    matches: query === "(pointer: coarse)" ? input.coarse : false,
  }),
  requestAnimationFrame: (callback) => callback(),
  fetch: async () => ({ok: true, status: 200}),
};

(async () => {
  vm.runInNewContext(input.script, context, {filename: "access.html"});
  await form.listeners.submit({preventDefault() {}});
  process.stdout.write(JSON.stringify({
    assigned: assignments.at(-1),
    order: threshold.children.map((child) => child.name),
    ownerOpen: owner.open,
    ownerSummary: summary.textContent,
    guestButton: guestButton.textContent,
    guestLede: guestLede.textContent,
    focused: document.activeElement && document.activeElement.name,
  }));
})().catch((error) => {
  process.stderr.write(error.stack);
  process.exitCode = 1;
});
"""


@pytest.fixture(scope="session")
def access_html() -> str:
    return ACCESS_HTML.read_text()


@pytest.fixture(scope="session")
def access_script(access_html: str) -> str:
    matches = re.findall(r"<script>(.*?)</script>", access_html, re.DOTALL)
    assert len(matches) == 1
    return matches[0]


def _run_access(
    access_script: str,
    search: str = "",
    *,
    guest_open: bool = True,
    coarse: bool = False,
) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node.js is required for the access client contract", pytrace=False)
    completed = subprocess.run(
        [node, "-e", HARNESS],
        input=json.dumps(
            {
                "script": access_script,
                "search": search,
                "guestOpen": guest_open,
                "coarse": coarse,
            }
        ),
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _next_query(value: str) -> str:
    return "?" + urlencode({"next": value})


def test_safe_local_next_preserves_query_and_fragment(access_script: str) -> None:
    target = "/room?tab=visits&name=a%20b#quiet"
    result = _run_access(access_script, _next_query(target))

    assert result["assigned"] == f"https://woolroom.test{target}"


@pytest.mark.parametrize(
    "target",
    [
        "https://outside.test/steal",
        "//outside.test/steal",
        "/\\outside.test/steal",
        "/\n/outside.test/steal",
    ],
)
def test_rejected_next_falls_back_to_root(
    access_script: str,
    target: str,
) -> None:
    result = _run_access(access_script, _next_query(target))

    assert result["assigned"] == "https://woolroom.test/"


def test_canonicalized_next_cannot_change_origin(access_script: str) -> None:
    target = "/safe/%2e%2e/%2e%2e//outside.test/steal"
    result = _run_access(access_script, _next_query(target))

    assigned = urlsplit(str(result["assigned"]))
    assert (assigned.scheme, assigned.netloc) == ("https", "woolroom.test")


def test_duplicate_next_parameters_fail_closed(access_script: str) -> None:
    search = "?" + urlencode(
        [("next", "/join/token"), ("next", "https://outside.test/steal")]
    )

    result = _run_access(access_script, search)

    assert result["assigned"] == "https://woolroom.test/"


def test_guest_open_defaults_to_guest_first(
    access_html: str,
    access_script: str,
) -> None:
    required_ids = {
        "access-form",
        "password",
        "status",
        "owner-access",
        "guest-button",
        "guest-description",
        "guest-status",
    }
    assert required_ids <= set(re.findall(r'\bid="([^"]+)"', access_html))
    assert access_html.index('class="access-guest"') < access_html.index(
        'id="owner-access"'
    )

    result = _run_access(access_script, guest_open=True)

    assert result["order"] == ["guest", "owner"]
    assert result["ownerOpen"] is False


def test_private_deployment_opens_and_focuses_owner(access_script: str) -> None:
    result = _run_access(access_script, guest_open=False, coarse=False)

    assert result["ownerOpen"] is True
    assert result["focused"] == "password"


def test_private_deployment_suppresses_coarse_pointer_autofocus(
    access_script: str,
) -> None:
    result = _run_access(access_script, guest_open=False, coarse=True)

    assert result["ownerOpen"] is True
    assert result["focused"] is None


def test_invite_entry_promotes_and_focuses_owner(access_script: str) -> None:
    result = _run_access(access_script, _next_query("/join/token"), coarse=False)

    assert result["order"] == ["owner", "guest"]
    assert result["ownerOpen"] is True
    assert result["ownerSummary"] == "enter as an invited human"
    assert result["guestButton"] == "visit the demo instead"
    assert result["guestLede"] == "just looking? the demo window is still open."
    assert result["focused"] == "password"


def test_coarse_pointer_suppresses_invite_autofocus(access_script: str) -> None:
    result = _run_access(access_script, _next_query("/join/token"), coarse=True)

    assert result["ownerOpen"] is True
    assert result["focused"] is None
