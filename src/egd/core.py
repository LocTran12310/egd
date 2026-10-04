"""EGD's rules in words — what each gate checks, who signs what, how a task moves.

The console's "How EGD works" page renders this. It sits next to the code it describes
(gates.py, tasks.py, proof/) and tests/test_core_info.py fails when a gate exists here
without a description, so the explanation cannot quietly drift from the behaviour.
"""

from __future__ import annotations

from .gates import GATE_ROLE
from .model import GATES, GATES_BY_TIER
from .proof import KINDS

ALL = ("lite", "standard", "full")
STD = ("standard", "full")
FULL = ("full",)


def _r(text: str, tiers=ALL) -> dict:
    return {"text": text, "tiers": list(tiers)}


GATE_INFO = {
    "frame": {
        "proves": "We know why this is worth doing.",
        "rules": [
            _r("brief.md has Problem, Outcome, Success signal and Out of scope — none empty, no TODO/TBD"),
            _r(".egd/map.md is filled in, not the blank template", STD),
            _r(".egd/map.md is signed by a person (reviewed_by)", STD),
        ],
    },
    "clarify": {
        "proves": "We know exactly what will be true when it is done.",
        "rules": [
            _r("At least one assumption is written down — every unasked question is one", STD),
            _r("No blocking assumption is still open"),
            _r("Every confirmed or rejected assumption says who settled it, and how"),
            _r("At least one acceptance criterion, each with given, when and then"),
        ],
        "records": "Fingerprints the acceptance criteria — changing them later needs an approved change request.",
    },
    "design": {
        "proves": "We know how it will work, and what goes wrong.",
        "rules": [
            _r("design.md has Approach, Alternatives considered and Failure modes, no TODO/TBD", STD),
            _r("No decision is still proposed", STD),
            _r("At least one accepted decision", FULL),
        ],
    },
    "slice": {
        "proves": "We know the order and the pieces.",
        "rules": [
            _r("Every acceptance criterion is covered by a slice; every slice has a demo and tasks"),
            _r("Every task belongs to a slice, has a positive estimate within the task limit and a done-when list"),
            _r("Dependencies exist and form no cycle; the longest chain inside a slice fits the slice limit"),
            _r("Every acceptance criterion names its test levels", STD),
            _r("Every task lists tests, and every acceptance criterion has a proof", FULL),
            _r("Acceptance criteria unchanged since clarify, or the change was approved"),
        ],
        "records": "Remembers the planned tasks and proofs — dropping one later needs an approved change request.",
    },
    "build": {
        "proves": "It works, on the code as it is now.",
        "rules": [
            _r("Every task is done: accepted by someone other than its submitter, or finished solo with a recorded reason"),
            _r("No change request is waiting for a decision"),
            _r("Every required proof passed on the current code and the current proof definition — older runs are stale"),
            _r("Nothing planned at slice was dropped without an approved change request"),
            _r("Acceptance criteria unchanged, or the change was approved"),
        ],
    },
    "accept": {
        "proves": "The client agrees.",
        "rules": [
            _r("Every slice has a passing UAT verdict from a client or PO", STD),
            _r("No critical or major defect is open", STD),
            _r("Acceptance criteria unchanged, or the change was approved", STD),
        ],
    },
    "release": {
        "proves": "It can ship — and be undone.",
        "rules": [
            _r("release.md says how to roll back"),
            _r("No assumption is still open and no decision still proposed"),
            _r("The plan is still valid and no critical or major defect is open"),
            _r("Acceptance criteria unchanged, or the change was approved"),
        ],
    },
}

TRANSITIONS = [
    {"cmd": "start", "from": "todo", "to": "doing", "who": "work",
     "checks": "The slice gate has passed and every dependency is done."},
    {"cmd": "submit", "from": "doing", "to": "review", "who": "work",
     "checks": "The submitter confirms each done-when item; the task's tests run; only declared files changed "
               "(or the drift is overridden with a reason)."},
    {"cmd": "accept", "from": "review", "to": "done", "who": "review",
     "checks": "Someone other than the submitter — under any of their names."},
    {"cmd": "reject", "from": "review", "to": "doing", "who": "review", "checks": "With a reason the submitter sees."},
    {"cmd": "solo", "from": "doing / review", "to": "done", "who": "work",
     "checks": "Same checks as submit, plus a reason; recorded as a review bypass."},
    {"cmd": "block", "from": "todo / doing / review", "to": "blocked", "who": "work",
     "checks": "With what it is waiting on."},
    {"cmd": "unblock", "from": "blocked", "to": "(where it was)", "who": "work",
     "checks": "Restores the state the block interrupted."},
    {"cmd": "reopen", "from": "done", "to": "doing", "who": "review", "checks": "With a reason."},
]

PROOF_KINDS = {
    "test": "Your test runner — its command, exit code and output.",
    "http": "An API call with assertions on status and JSON fields; a request/response transcript.",
    "cli": "A command — job, migration, data check — with assertions on exit code and output.",
    "ui": "A browser walk through pages with assertions; screenshots per viewport and a contact sheet.",
}

TRUST = [
    {"title": "One event per file",
     "text": "Every move is a JSON file in events/ with who, when and what. State is replayed from them; there "
             "is no status field to edit, and teammates on different branches never conflict."},
    {"title": "Signatures are people",
     "text": "Every --by is recorded. Roles in team.toml decide who may pass gates, review, sign UAT or approve "
             "change requests. Agents work tasks; people sign."},
    {"title": "Evidence, not claims",
     "text": "Proofs run against a known commit. Change the code or the proof and the old run no longer counts."},
    {"title": "Scope is a contract",
     "text": "After clarify, acceptance criteria are fingerprinted. Editing them, or dropping planned work, "
             "closes the following gates until the client approves a change request."},
]


# ---------------------------------------------------------------- Vietnamese
# Same order and count as the English above; tiers, commands, roles and file names stay English.
GATE_INFO_VI = {
    "frame": {
        "proves": "Biết rõ vì sao việc này đáng làm.",
        "rules": [
            "brief.md có đủ Problem, Outcome, Success signal và Out of scope — không mục nào trống, không còn TODO/TBD",
            ".egd/map.md đã được điền, không còn là mẫu trống",
            ".egd/map.md đã được một người ký (reviewed_by)",
        ],
    },
    "clarify": {
        "proves": "Biết chính xác điều gì sẽ đúng khi làm xong.",
        "rules": [
            "Có ít nhất một giả định được ghi lại — mỗi câu hỏi chưa hỏi là một giả định",
            "Không còn giả định chặn (blocking) nào đang mở",
            "Mỗi giả định đã xác nhận hoặc bác bỏ đều ghi rõ ai quyết và quyết thế nào",
            "Có ít nhất một tiêu chí chấp nhận, mỗi tiêu chí có đủ given, when và then",
        ],
        "records": "Lấy dấu vân tay các tiêu chí chấp nhận — sửa chúng về sau cần một yêu cầu thay đổi được duyệt.",
    },
    "design": {
        "proves": "Biết nó sẽ chạy thế nào, và chỗ nào có thể hỏng.",
        "rules": [
            "design.md có Approach, Alternatives considered và Failure modes, không còn TODO/TBD",
            "Không còn quyết định nào ở trạng thái đề xuất (proposed)",
            "Có ít nhất một quyết định đã được chấp nhận",
        ],
    },
    "slice": {
        "proves": "Biết thứ tự và từng phần việc.",
        "rules": [
            "Mỗi tiêu chí chấp nhận đều được một slice bao phủ; mỗi slice có demo và task",
            "Mỗi task thuộc một slice, có ước lượng dương trong giới hạn task và có danh sách done-when",
            "Các phụ thuộc đều tồn tại và không tạo vòng; chuỗi dài nhất trong một slice nằm trong giới hạn slice",
            "Mỗi tiêu chí chấp nhận ghi rõ các cấp độ kiểm thử",
            "Mỗi task liệt kê test, và mỗi tiêu chí chấp nhận có một proof",
            "Tiêu chí chấp nhận không đổi từ sau clarify, hoặc thay đổi đã được duyệt",
        ],
        "records": "Ghi nhớ các task và proof đã lên kế hoạch — bỏ bớt về sau cần một yêu cầu thay đổi được duyệt.",
    },
    "build": {
        "proves": "Nó chạy được, trên đúng code hiện tại.",
        "rules": [
            "Mọi task đều xong: được một người khác người nộp chấp nhận, hoặc làm solo có ghi lý do",
            "Không có yêu cầu thay đổi nào đang chờ quyết định",
            "Mọi proof bắt buộc đã pass trên code hiện tại và định nghĩa proof hiện tại — lần chạy cũ bị coi là stale",
            "Không có việc nào đã lên kế hoạch ở slice bị bỏ mà thiếu yêu cầu thay đổi được duyệt",
            "Tiêu chí chấp nhận không đổi, hoặc thay đổi đã được duyệt",
        ],
    },
    "accept": {
        "proves": "Khách hàng đồng ý.",
        "rules": [
            "Mỗi slice có kết quả UAT đạt từ client hoặc PO",
            "Không còn lỗi critical hoặc major nào đang mở",
            "Tiêu chí chấp nhận không đổi, hoặc thay đổi đã được duyệt",
        ],
    },
    "release": {
        "proves": "Có thể phát hành — và có thể hoàn tác.",
        "rules": [
            "release.md ghi rõ cách rollback",
            "Không còn giả định nào đang mở và không còn quyết định nào ở trạng thái đề xuất",
            "Kế hoạch vẫn hợp lệ và không còn lỗi critical hoặc major nào đang mở",
            "Tiêu chí chấp nhận không đổi, hoặc thay đổi đã được duyệt",
        ],
    },
}

_STATE_VI = {"todo": "cần làm", "doing": "đang làm", "review": "chờ review", "done": "xong", "blocked": "bị chặn",
             "(where it was)": "(trạng thái trước đó)"}

TRANSITIONS_VI = {
    "start": "Gate slice đã pass và mọi phụ thuộc đã xong.",
    "submit": "Người nộp xác nhận từng mục done-when; test của task được chạy; chỉ các file đã khai báo bị thay đổi "
              "(hoặc phần lệch được bỏ qua kèm lý do).",
    "accept": "Do một người khác người nộp — dưới bất kỳ tên nào của họ.",
    "reject": "Kèm lý do mà người nộp sẽ thấy.",
    "solo": "Kiểm tra giống submit, thêm một lý do; được ghi lại là bỏ qua review.",
    "block": "Kèm điều nó đang chờ.",
    "unblock": "Khôi phục trạng thái mà lệnh block đã ngắt.",
    "reopen": "Kèm lý do.",
}

PROOF_KINDS_VI = {
    "test": "Test runner của bạn — lệnh, mã thoát và output.",
    "http": "Một lời gọi API với assertion trên status và các trường JSON; kèm bản ghi request/response.",
    "cli": "Một lệnh — job, migration, kiểm tra dữ liệu — với assertion trên mã thoát và output.",
    "ui": "Đi qua các trang trên trình duyệt với assertion; ảnh chụp từng viewport và một contact sheet.",
}

TRUST_VI = [
    {"title": "Mỗi sự kiện một file",
     "text": "Mỗi bước đi là một file JSON trong events/ ghi ai, khi nào, làm gì. Trạng thái được dựng lại từ đó; "
             "không có trường status nào để sửa, và đồng đội trên các nhánh khác nhau không bao giờ xung đột."},
    {"title": "Chữ ký là của con người",
     "text": "Mọi --by đều được ghi lại. Vai trò trong team.toml quyết định ai được pass gate, review, ký UAT hay "
             "duyệt yêu cầu thay đổi. Agent làm task; con người ký."},
    {"title": "Bằng chứng, không phải lời hứa",
     "text": "Proof chạy trên một commit xác định. Đổi code hoặc đổi proof thì lần chạy cũ không còn được tính."},
    {"title": "Phạm vi là hợp đồng",
     "text": "Sau clarify, tiêu chí chấp nhận được lấy dấu vân tay. Sửa chúng, hoặc bỏ bớt việc đã lên kế hoạch, "
             "sẽ đóng các gate phía sau cho đến khi khách hàng duyệt một yêu cầu thay đổi."},
]

ANY_MEMBER = {"en": "any member", "vi": "mọi thành viên"}


def _state_vi(s: str) -> str:
    return " / ".join(_STATE_VI.get(x, x) for x in s.split(" / "))


def _gate_info(g: str, lang: str) -> dict:
    info = GATE_INFO[g]
    if lang != "vi":
        return info
    vi = GATE_INFO_VI[g]
    out = {"proves": vi["proves"],
           "rules": [{"text": text, "tiers": rule["tiers"]} for text, rule in zip(vi["rules"], info["rules"])]}
    if "records" in info:
        out["records"] = vi["records"]
    return out


def _transition(t: dict, lang: str) -> dict:
    if lang != "vi":
        return t
    return {**t, "from": _state_vi(t["from"]), "to": _state_vi(t["to"]), "checks": TRANSITIONS_VI[t["cmd"]]}


def core_info(roles: dict, lang: str = "en") -> dict:
    """The rules in words, in English (default) or Vietnamese ("vi") — same shape either way."""
    lang = "vi" if lang == "vi" else "en"
    kinds = PROOF_KINDS_VI if lang == "vi" else PROOF_KINDS
    return {
        "tiers": {t: list(g) for t, g in GATES_BY_TIER.items()},
        "gates": [{"name": g, **_gate_info(g, lang), "signs": roles.get(GATE_ROLE.get(g, "gate"), [])} for g in GATES],
        "transitions": [{**_transition(t, lang),
                         "roles": roles.get(t["who"], []) if t["who"] != "work" else [ANY_MEMBER[lang]]}
                        for t in TRANSITIONS],
        "proof_kinds": [{"kind": k, "text": kinds[k]} for k in KINDS],
        "trust": TRUST_VI if lang == "vi" else TRUST,
        "roles": roles,
    }
