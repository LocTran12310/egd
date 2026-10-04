/* EGD UI — the "How EGD works" guide page. Owns its own sections and diagrams.
   Static text lives in GUIDE (en / vi); live rules come from api/core when the guide opens
   (a snapshot carries them as P.core / P.core_vi).
   Diagrams are inline SVG coloured by the theme tokens, so they work offline and in both themes. */

const G_GATES = ["frame", "clarify", "design", "slice", "build", "accept", "release"];
const G_TIERS = { lite: ["frame", "clarify", "slice", "build", "release"], standard: G_GATES, full: G_GATES };
const G_TABS = ["overview", "arch", "flows", "task", "gates", "roles", "glossary"];

const GUIDE = {
  en: {
    tabs: { overview: "Overview", arch: "Architecture", flows: "Flows", task: "Task lifecycle", gates: "Gates & rules", roles: "Roles & signing", glossary: "Glossary" },
    tablist: "Guide sections",
    noCore: "Update egd to see the live rules here.",
    coreFailed: "Could not load the rules.", retry: "Try again",
    overview: {
      kicker: "How EGD works",
      title: "Evidence-Gated Delivery",
      lede: "EGD keeps a feature honest from idea to release. People write the **plan** in files, every move is written to an append-only **trail**, and each **gate** is computed from both. Nobody — person or agent — can declare work done: it is done when the rules hold.",
      cards: [
        ["Plan", "Plan in files", "Brief, acceptance criteria, design, slices, tasks and proofs live in `.egd/features/<feature>/`, reviewed and committed like code."],
        ["Trail", "Trail of events", "Every start, submit, accept, gate pass, UAT verdict, change request and proof run is one JSON file in `events/`: who, when, what. Never edited."],
        ["Gates", "Gates are computed", "Seven gates are recalculated from the plan and the trail. A closed gate says exactly why; only a person with the right role can pass an open one."],
        ["Evidence", "Evidence, not claims", "Proofs run against a known commit and proof definition. Change either one and the old run no longer counts."],
      ],
      rulesTitle: "Three rules that make it work",
      rules: [
        ["The CLI and the console are the authority", "Run `egd status` (or open the console) before anything else. It shows the next gate and exactly why it is closed — never guess the state from memory."],
        ["Never edit `events/` by hand", "The trail is append-only. Every change goes through an `egd` command or a console action, which writes a new event file."],
        ["Never sign for someone else", "`--by` is always your own name. An agent never signs for a person: agents work tasks; people pass gates, review, sign UAT and approve change requests."],
      ],
      tiersTitle: "Tiers — how much process a feature gets",
      tiersHead: ["Tier", "For", "Gates"],
      tierFor: {
        lite: "A bugfix or work under a day. Skips design and accept.",
        standard: "A normal feature. All seven gates.",
        full: "Fixed-price or risky work. Every acceptance criterion needs a proof and every task lists its tests.",
      },
    },
    arch: {
      title: "Architecture",
      lead: "Everything EGD knows lives in files inside each repository. The CLI and the console are two doors onto the same files; the gate engine reads them and computes where each feature stands.",
      d: {
        agents: "Agents", agentsSub: "work tasks only",
        people: "People", peopleSub: "pm · lead · dev · qa · client · po",
        cli: "CLI  egd", cliSub: "start · submit · pass · proof run",
        console: "Web console", consoleSub: "egd console serve",
        egd: ".egd/  in each repository",
        plan: "plan.toml", planSub: "assumptions · ACs · slices · tasks",
        docs: "brief · design · proof · release", docsSub: "the files people review",
        events: "events/", eventsSub: "one JSON file per event",
        committed: "all committed with git",
        engine: "Gate engine", engineSub: "replays plan + trail",
        gates: "7 gates", gatesSub: "open / closed + why",
        shown: "shown in the CLI and the console",
        proofs: "Proof runs", proofsSub: "recorded as events",
        envs: "runs against", prod: "prod · read-only",
        multi: "One console, many repositories",
        multi1: "EGD repos — act here; events land in that repo",
        multi2: "ai-dlc repos — read-only",
        dash: "Dashboard mode — one repository, read-only",
        dash1: "egd serve · egd site · egd board --html",
        writes: "writes", reads: "reads", recorded: "recorded",
      },
      whereTitle: "Where things live",
      whereHead: ["Path", "What it holds", "Committed"],
      yes: "yes", no: "no",
      where: [
        [".egd/config.toml", "Limits, test command, roles, environments", 1],
        [".egd/team.toml", "Who is on the team and the roles each person has", 1],
        [".egd/map.md", "Repository map — a person reads it and signs `reviewed_by`", 1],
        ["features/<feature>/plan.toml", "Assumptions, acceptance criteria, decisions, slices, tasks", 1],
        ["brief.md · design.md · proof.toml · release.md", "Why, how, which proofs, and how to roll back", 1],
        ["features/<feature>/events/", "The trail — one JSON file per event", 1],
        ["features/<feature>/runs/", "Proof transcripts and screenshots", 0],
      ],
      treeTitle: "The folder",
      tree: [
        [".egd/", ""],
        ["├── config.toml", "limits, test command, roles, environments"],
        ["├── team.toml", "who may sign what"],
        ["├── map.md", "repository map — a person signs it"],
        ["└── features/<feature>/", ""],
        ["    ├── brief.md  plan.toml  design.md  proof.toml  release.md", ""],
        ["    ├── events/", "the trail — committed, one JSON file per event"],
        ["    └── runs/", "proof transcripts and screenshots (not committed)"],
      ],
    },
    flows: {
      title: "Flows",
      a: {
        title: "A feature through the seven gates",
        lead: "Each gate opens when its rules hold; a person with the right role then passes it.",
        short: { frame: "why", clarify: "what exactly", design: "how", slice: "order & pieces", build: "works now", accept: "client agrees", release: "ship & undo" },
        cr: "ACs edited after clarify → later gates close until the client approves a change request",
        clarifyNote: "fingerprints the ACs", sliceNote: "remembers tasks & proofs",
        uat: "UAT per slice", uatSub: "client / PO",
        lite: "lite tier: frame → clarify → slice → build → release (design and accept are skipped)",
      },
      b: {
        title: "Is a proof still fresh?",
        lead: "A passing run counts only for the code and the proof definition it ran against.",
        run: "egd proof run", runSub: "test · http · cli · ui",
        rec: "Recorded as an event", rec1: "pass or fail", rec2: "commit sha + proof hash",
        fresh: "Fresh", freshSub: "counts for build",
        same: "nothing changed",
        change: "Code or proof changes", changeSub: "new commit, or proof.toml edited",
        stale: "Stale", staleSub: "the old run no longer counts",
        closed: "build closed", closedSub: "until re-run",
        rerun: "re-run on the current code",
      },
      c: {
        title: "From the inbox to a commit",
        lead: "The console gathers what needs a person across every repository. Acting writes the same event the CLI would, inside that repository.",
        repos: "Repositories", reposSub: "EGD · ai-dlc",
        // the inbox and its sections are named as the console names them (t()), in either language
        decide: "only a person can", unblock: "work is stuck", hygiene: "keeps the view honest",
        action: "Action", action1: "accept · pass · UAT · CR…", action2: "signed as you, or as", action3: "the repository's signer",
        event: "Event file", event1: "events/….json", event2: "in that repo",
        commit: "Commit", commitSub: "by you",
        ro: "ai-dlc repositories are read-only: they show up in the console but offer no actions.",
      },
    },
    task: {
      title: "Task lifecycle",
      lead: "A task moves only through commands. Each move is an event: checked, signed and kept.",
      states: { todo: "To do", doing: "Doing", review: "Review", done: "Done", blocked: "Blocked" },
      reject: "reject", reopen: "reopen", solo: "solo — from doing or review, recorded as a bypass",
      blockSub: "block / unblock from to do, doing or review — unblock returns to where it was",
      transTitle: "Transitions",
      transHead: ["Command", "From → to", "Who may sign", "What is checked"],
    },
    gates: {
      title: "The seven gates",
      lead: "These rules come from the running egd. Switch the tier to see which ones apply.",
      tier: "Tier", tierHint: { lite: "bugfix, under a day", standard: "normal feature", full: "fixed-price, risky" },
      notIn: "not in {tier}", signedBy: "Signed by",
      trustTitle: "Why you can trust a gate", kinds: "Four kinds of proof",
      liveTitle: "See it on a feature", feature: "Feature",
      released: "Released — every gate passed.", closed: "is closed because:", open: "is open — a person can pass it.",
      next: "next", openFeature: "Open feature", none: "No features yet.",
    },
    roles: {
      title: "Roles & signing",
      lead: "Every signature is a person's name. Roles in `team.toml` decide who may sign what. With no members listed, anyone may sign anything — and the trail still records who did.",
      head: ["Action", "Default roles", "What it covers"],
      what: {
        gate: "Pass frame, clarify, design, slice, build and release",
        review: "Accept, reject or reopen a task",
        uat: "UAT verdicts and the accept gate",
        cr_approve: "Approve or reject change requests",
        work: "Start, submit, solo, block and unblock a task",
        agent: "Works tasks only — never passes gates, reviews, signs UAT or approves change requests",
      },
      anyMember: "any member", agents: "agents",
      teamTitle: "team.toml",
      teamLead: "Names, aliases and roles. Which role may do what is set in `[roles]` in `config.toml`.",
      signerTitle: "Per-repository signer",
      signer: [
        "The console signs every action with your default name (`egd console --user <name>`).",
        "A repository that needs another name gets its own signer: open **Repositories** and edit **Signs as**, or run `egd console signer <repo> --name \"Minh Le\"`.",
        "An empty name goes back to the default. The console warns when the name is not in that repository's `team.toml`.",
      ],
      sodTitle: "Separation of duties",
      sod: [
        ["The submitter cannot accept", "Someone other than the person who submitted a task must review it."],
        ["Aliases are the same person", "A name, a GitHub handle and the aliases in `team.toml` all count as one reviewer, not several."],
        ["Skipping review is visible", "`egd solo` finishes a task without review, but it needs a reason and is recorded as a bypass."],
      ],
    },
    glossary: {
      title: "Glossary",
      lead: "The words EGD uses, in one place.",
      terms: [
        ["feature", "A unit of delivery with its own folder in `.egd/features/`, a tier and seven gates."],
        ["slice", "A demoable piece of a feature. It covers acceptance criteria, holds tasks and gets a UAT verdict."],
        ["task", "A few hours of work inside a slice, with an estimate, the files it touches and a done-when list."],
        ["AC — acceptance criterion", "What will be true when the feature is done, written as given / when / then."],
        ["assumption", "A question nobody has answered yet, written down. A blocking one keeps clarify closed until someone settles it and says how."],
        ["decision", "A design choice. It stays proposed until accepted; open proposals keep design and release closed."],
        ["proof", "Executable evidence for acceptance criteria, declared in `proof.toml`. Four kinds:"],
        ["trail · event", "The append-only history: one JSON file per event in `events/`. Every state is replayed from it."],
        ["gate", "A checkpoint computed from the plan and the trail: frame, clarify, design, slice, build, accept, release."],
        ["tier", "lite, standard or full — which gates run and how strict their rules are."],
        ["CR — change request", "How scope changes after clarify. The following gates stay closed until the client or PO approves it."],
        ["defect · leakage", "A defect is a bug recorded with a severity; an open critical or major one blocks accept and release. Leakage is the share of defects found in UAT or production."],
        ["UAT", "User acceptance testing: the client or PO records a pass or fail verdict for each slice."],
        ["map.md · reviewed_by", "The repository map. A person reads it and writes their name in `reviewed_by`; frame (standard and full) waits for it."],
        ["scope fingerprint", "Taken when clarify passes (acceptance criteria) and slice passes (planned tasks and proofs). Changing that scope later needs an approved CR."],
        ["stale proof", "A run made on older code or an older proof definition. It no longer counts for build."],
        ["solo", "Finishing a task without review, with a reason. Recorded as a review bypass."],
        ["console", "`egd console` — one web page across many repositories, where people act on the inbox."],
        ["dashboard", "A read-only view of one repository: `egd serve`, `egd site` or `egd board --html`."],
        ["signer", "The name console actions are signed with: your default name, or a per-repository signer."],
        ["import aidlc", "`egd import aidlc --by <name>` turns each `.ai/` feature into an EGD feature and copies its history into the trail. It only reads `.ai/` and commits nothing."],
      ],
    },
  },

  vi: {
    tabs: { overview: "Tổng quan", arch: "Kiến trúc", flows: "Luồng", task: "Vòng đời task", gates: "Cổng & quy tắc", roles: "Vai trò & chữ ký", glossary: "Thuật ngữ" },
    tablist: "Các phần của hướng dẫn",
    noCore: "Hãy cập nhật egd để xem quy tắc thực tế ở đây.",
    coreFailed: "Không tải được quy tắc.", retry: "Thử lại",
    overview: {
      kicker: "EGD hoạt động thế nào",
      title: "Evidence-Gated Delivery",
      lede: "EGD giữ cho một feature minh bạch từ ý tưởng đến lúc release. Con người viết **kế hoạch** trong file, mọi thao tác được ghi vào **nhật ký** (trail) chỉ thêm không sửa, và mỗi **cổng** (gate) được tính từ cả hai. Không ai — người hay agent — tự tuyên bố là xong: việc chỉ xong khi các quy tắc đều thỏa.",
      cards: [
        ["Kế hoạch", "Kế hoạch nằm trong file", "Brief, tiêu chí nghiệm thu, thiết kế, slice, task và proof nằm trong `.egd/features/<feature>/`, được review và commit như code."],
        ["Nhật ký", "Mỗi sự kiện một file", "Start, submit, accept, pass cổng, kết quả UAT, CR, lần chạy proof — mỗi việc là một file JSON trong `events/`: ai, lúc nào, làm gì. Không bao giờ sửa."],
        ["Cổng", "Cổng được tính, không khai", "Bảy cổng được tính lại từ kế hoạch và nhật ký. Cổng đóng thì nói rõ vì sao; cổng mở thì chỉ người có đúng vai trò mới được pass."],
        ["Bằng chứng", "Bằng chứng thay cho lời nói", "Proof chạy trên một commit và một định nghĩa proof cụ thể. Đổi một trong hai là lần chạy cũ hết giá trị."],
      ],
      rulesTitle: "Ba quy tắc giữ cho mọi thứ vận hành",
      rules: [
        ["CLI và console là nguồn sự thật", "Luôn chạy `egd status` (hoặc mở console) trước khi làm gì. Lệnh này cho biết cổng tiếp theo và lý do nó đang đóng — đừng đoán trạng thái theo trí nhớ."],
        ["Không sửa tay thư mục `events/`", "Nhật ký chỉ thêm, không sửa. Mọi thay đổi đều đi qua lệnh `egd` hoặc thao tác trên console, và mỗi lần như vậy ghi ra một file sự kiện mới."],
        ["Không ký thay người khác", "`--by` luôn là tên của chính bạn. Agent không bao giờ ký thay người: agent làm task, còn con người pass cổng, review, ký UAT và duyệt CR."],
      ],
      tiersTitle: "Tier — feature cần bao nhiêu quy trình",
      tiersHead: ["Tier", "Dùng cho", "Các cổng"],
      tierFor: {
        lite: "Bugfix hoặc việc dưới một ngày. Bỏ qua design và accept.",
        standard: "Feature bình thường. Đi đủ bảy cổng.",
        full: "Dự án fixed-price hoặc rủi ro cao. Mỗi tiêu chí nghiệm thu phải có proof, mỗi task phải liệt kê test.",
      },
    },
    arch: {
      title: "Kiến trúc",
      lead: "Mọi thứ EGD biết đều nằm trong file của từng repo. CLI và console là hai cửa vào cùng một bộ file; bộ máy tính cổng đọc chúng và cho biết mỗi feature đang ở đâu.",
      d: {
        agents: "Agent", agentsSub: "chỉ làm task",
        people: "Con người", peopleSub: "pm · lead · dev · qa · client · po",
        cli: "CLI  egd", cliSub: "start · submit · pass · proof run",
        console: "Web console", consoleSub: "egd console serve",
        egd: ".egd/  trong mỗi repo",
        plan: "plan.toml", planSub: "giả định · AC · slice · task",
        docs: "brief · design · proof · release", docsSub: "các file con người review",
        events: "events/", eventsSub: "mỗi sự kiện một file JSON",
        committed: "tất cả đều commit vào git",
        engine: "Bộ tính cổng", engineSub: "dựng lại từ plan + trail",
        gates: "7 cổng", gatesSub: "mở / đóng + lý do",
        shown: "hiện trên CLI và console",
        proofs: "Lần chạy proof", proofsSub: "ghi lại thành sự kiện",
        envs: "chạy trên", prod: "prod · chỉ đọc",
        multi: "Một console, nhiều repo",
        multi1: "Repo EGD — thao tác được; sự kiện ghi vào repo đó",
        multi2: "Repo ai-dlc — chỉ đọc",
        dash: "Chế độ dashboard — một repo, chỉ đọc",
        dash1: "egd serve · egd site · egd board --html",
        writes: "ghi", reads: "đọc", recorded: "ghi lại",
      },
      whereTitle: "Cái gì nằm ở đâu",
      whereHead: ["Đường dẫn", "Chứa gì", "Commit"],
      yes: "có", no: "không",
      where: [
        [".egd/config.toml", "Giới hạn, lệnh test, vai trò, môi trường", 1],
        [".egd/team.toml", "Ai trong team và mỗi người có vai trò gì", 1],
        [".egd/map.md", "Bản đồ repo — một người đọc lại rồi ký `reviewed_by`", 1],
        ["features/<feature>/plan.toml", "Giả định, tiêu chí nghiệm thu, quyết định, slice, task", 1],
        ["brief.md · design.md · proof.toml · release.md", "Vì sao, làm thế nào, proof nào, rollback ra sao", 1],
        ["features/<feature>/events/", "Nhật ký — mỗi sự kiện một file JSON", 1],
        ["features/<feature>/runs/", "Transcript và screenshot của proof", 0],
      ],
      treeTitle: "Cấu trúc thư mục",
      tree: [
        [".egd/", ""],
        ["├── config.toml", "giới hạn, lệnh test, vai trò, môi trường"],
        ["├── team.toml", "ai được ký việc gì"],
        ["├── map.md", "bản đồ repo — một người ký xác nhận"],
        ["└── features/<feature>/", ""],
        ["    ├── brief.md  plan.toml  design.md  proof.toml  release.md", ""],
        ["    ├── events/", "nhật ký — được commit, mỗi sự kiện một file JSON"],
        ["    └── runs/", "transcript và screenshot của proof (không commit)"],
      ],
    },
    flows: {
      title: "Luồng",
      a: {
        title: "Một feature đi qua bảy cổng",
        lead: "Mỗi cổng mở khi các quy tắc của nó thỏa; sau đó người có đúng vai trò sẽ pass.",
        short: { frame: "vì sao", clarify: "chính xác là gì", design: "làm thế nào", slice: "thứ tự, phần việc", build: "chạy đúng chưa", accept: "khách đồng ý", release: "ship & rollback" },
        cr: "Sửa AC sau clarify → các cổng sau đóng lại cho đến khi khách duyệt CR",
        clarifyNote: "ghi dấu bộ AC", sliceNote: "ghi nhớ task & proof",
        uat: "UAT từng slice", uatSub: "khách / PO",
        lite: "tier lite: frame → clarify → slice → build → release (bỏ qua design và accept)",
      },
      b: {
        title: "Proof còn mới không?",
        lead: "Một lần chạy pass chỉ có giá trị với đúng code và đúng định nghĩa proof lúc chạy.",
        run: "egd proof run", runSub: "test · http · cli · ui",
        rec: "Ghi lại thành sự kiện", rec1: "pass hoặc fail", rec2: "commit sha + hash của proof",
        fresh: "Còn mới", freshSub: "được tính cho build",
        same: "không đổi gì",
        change: "Code hoặc proof thay đổi", changeSub: "commit mới, hoặc sửa proof.toml",
        stale: "Stale", staleSub: "lần chạy cũ hết giá trị",
        closed: "build đóng", closedSub: "đến khi chạy lại",
        rerun: "chạy lại trên code hiện tại",
      },
      c: {
        title: "Từ inbox đến commit",
        lead: "Console gom mọi việc cần con người trên tất cả repo. Thao tác trên console ghi ra đúng sự kiện mà CLI sẽ ghi, ngay trong repo đó.",
        repos: "Các repo", reposSub: "EGD · ai-dlc",
        decide: "chỉ người quyết", unblock: "việc đang kẹt", hygiene: "giữ số liệu đúng",
        action: "Thao tác", action1: "accept · pass · UAT · CR…", action2: "ký bằng tên bạn, hoặc", action3: "tên ký riêng của repo",
        event: "File sự kiện", event1: "events/….json", event2: "trong repo đó",
        commit: "Commit", commitSub: "do bạn làm",
        ro: "Repo ai-dlc chỉ đọc: vẫn hiện trên console nhưng không có thao tác nào.",
      },
    },
    task: {
      title: "Vòng đời task",
      lead: "Task chỉ di chuyển qua lệnh. Mỗi bước là một sự kiện: được kiểm tra, được ký và được lưu lại.",
      states: { todo: "Cần làm", doing: "Đang làm", review: "Chờ review", done: "Xong", blocked: "Bị chặn" },
      reject: "reject", reopen: "reopen", solo: "solo — từ doing hoặc review, ghi nhận là bỏ qua review",
      blockSub: "block / unblock từ cần làm, đang làm hoặc chờ review — unblock trả về đúng chỗ cũ",
      transTitle: "Các bước chuyển",
      transHead: ["Lệnh", "Từ → đến", "Ai được ký", "Kiểm tra gì"],
    },
    gates: {
      title: "Bảy cổng",
      lead: "Các quy tắc dưới đây lấy từ egd đang chạy. Đổi tier để xem quy tắc nào áp dụng.",
      tier: "Tier", tierHint: { lite: "bugfix, dưới một ngày", standard: "feature bình thường", full: "fixed-price, rủi ro" },
      notIn: "không có trong {tier}", signedBy: "Người ký",
      trustTitle: "Vì sao có thể tin một cổng", kinds: "Bốn loại proof",
      liveTitle: "Xem trên một feature thật", feature: "Feature",
      released: "Đã release — mọi cổng đều đã pass.", closed: "đang đóng vì:", open: "đang mở — một người có thể pass.",
      next: "tiếp theo", openFeature: "Mở feature", none: "Chưa có feature nào.",
    },
    roles: {
      title: "Vai trò & chữ ký",
      lead: "Mỗi chữ ký là tên một người. Vai trò trong `team.toml` quyết định ai được ký việc gì. Nếu chưa khai báo thành viên nào thì ai cũng ký được mọi thứ — và nhật ký vẫn ghi rõ ai đã làm.",
      head: ["Việc", "Vai trò mặc định", "Gồm những gì"],
      what: {
        gate: "Pass frame, clarify, design, slice, build và release",
        review: "Accept, reject hoặc reopen một task",
        uat: "Ghi kết quả UAT và pass cổng accept",
        cr_approve: "Duyệt hoặc từ chối CR",
        work: "Start, submit, solo, block và unblock một task",
        agent: "Chỉ làm task — không pass cổng, không review, không ký UAT, không duyệt CR",
      },
      anyMember: "mọi thành viên", agents: "agent",
      teamTitle: "team.toml",
      teamLead: "Tên, bí danh và vai trò. Vai trò nào được làm việc gì thì khai trong `[roles]` của `config.toml`.",
      signerTitle: "Tên ký theo từng repo",
      signer: [
        "Console ký mọi thao tác bằng tên mặc định của bạn (`egd console --user <tên>`).",
        "Repo nào cần ký tên khác thì đặt tên ký riêng: vào **Repository**, sửa cột **Ký tên**, hoặc chạy `egd console signer <repo> --name \"Minh Le\"`.",
        "Để trống là quay về tên mặc định. Console sẽ cảnh báo nếu tên đó không có trong `team.toml` của repo.",
      ],
      sodTitle: "Tách bạch trách nhiệm",
      sod: [
        ["Người submit không tự accept", "Task phải được một người khác review, không phải người đã submit."],
        ["Bí danh vẫn là một người", "Tên, GitHub handle và các bí danh trong `team.toml` đều tính là một người review, không phải nhiều người."],
        ["Bỏ qua review thì ai cũng thấy", "`egd solo` cho phép hoàn thành task mà không cần review, nhưng phải có lý do và được ghi lại là bỏ qua review."],
      ],
    },
    glossary: {
      title: "Thuật ngữ",
      lead: "Những từ EGD dùng, gom về một chỗ.",
      terms: [
        ["feature", "Một đơn vị giao hàng, có thư mục riêng trong `.egd/features/`, một tier và bảy cổng."],
        ["slice", "Một phần của feature có thể demo được. Slice bao phủ các tiêu chí nghiệm thu, chứa task và được khách chấm UAT."],
        ["task", "Vài giờ làm việc trong một slice, có ước lượng, danh sách file sẽ đụng tới và danh sách done-when."],
        ["AC — tiêu chí nghiệm thu", "Điều sẽ đúng khi feature xong, viết theo dạng given / when / then."],
        ["giả định (assumption)", "Một câu hỏi chưa ai trả lời, được ghi lại. Giả định chặn (blocking) giữ cổng clarify đóng cho đến khi có người chốt và ghi rõ chốt thế nào."],
        ["quyết định (decision)", "Một lựa chọn thiết kế. Còn ở trạng thái proposed cho đến khi được accept; đề xuất còn mở sẽ giữ design và release đóng."],
        ["proof", "Bằng chứng chạy được cho các tiêu chí nghiệm thu, khai báo trong `proof.toml`. Có bốn loại:"],
        ["trail · sự kiện", "Lịch sử chỉ thêm không sửa: mỗi sự kiện một file JSON trong `events/`. Mọi trạng thái đều được dựng lại từ đây."],
        ["cổng (gate)", "Điểm kiểm soát được tính từ kế hoạch và nhật ký: frame, clarify, design, slice, build, accept, release."],
        ["tier", "lite, standard hoặc full — cổng nào chạy và quy tắc chặt đến đâu."],
        ["CR — yêu cầu thay đổi", "Cách thay đổi phạm vi sau clarify. Các cổng phía sau đóng cho đến khi khách hoặc PO duyệt."],
        ["defect · leakage", "Defect là bug được ghi kèm mức độ nghiêm trọng; còn defect critical hoặc major đang mở thì accept và release bị chặn. Leakage là tỉ lệ defect bị phát hiện ở UAT hoặc production."],
        ["UAT", "Kiểm thử nghiệm thu: khách hoặc PO ghi kết quả pass hay fail cho từng slice."],
        ["map.md · reviewed_by", "Bản đồ repo. Một người đọc lại rồi ghi tên vào `reviewed_by`; cổng frame (standard và full) chờ việc này."],
        ["dấu vân tay phạm vi", "Được ghi khi pass clarify (bộ AC) và pass slice (task và proof đã lên kế hoạch). Sau đó muốn đổi phạm vi phải có CR được duyệt."],
        ["proof stale", "Lần chạy trên code cũ hoặc định nghĩa proof cũ. Không còn được tính cho build."],
        ["solo", "Hoàn thành task mà không qua review, kèm lý do. Được ghi lại là bỏ qua review."],
        ["console", "`egd console` — một trang web cho nhiều repo, nơi mọi người xử lý inbox."],
        ["dashboard", "Màn hình chỉ đọc cho một repo: `egd serve`, `egd site` hoặc `egd board --html`."],
        ["signer (tên ký)", "Tên dùng để ký các thao tác trên console: tên mặc định của bạn, hoặc tên ký riêng của từng repo."],
        ["import aidlc", "`egd import aidlc --by <tên>` chuyển mỗi feature trong `.ai/` thành một feature EGD và chép lịch sử sang nhật ký. Lệnh chỉ đọc `.ai/` và không commit gì."],
      ],
    },
  },
};

const TEAM_EXAMPLE = `[[member]]
name = "Linh"
github = "linh-pm"
aliases = ["Linh Nguyen"]
roles = ["pm", "lead", "qa"]

[[member]]
name = "Bao"
roles = ["dev"]

[[member]]
name = "Globex CFO"
roles = ["client"]`;

// ---------------------------------------------------------------- small helpers
/* GUIDE strings are authored here: escape, then allow **bold** and `code` */
const gmd = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
const gChips = list => list.map(x => `<span class="chip">${esc(x)}</span>`).join(" ");
const gSec = (title, lead, extra = "") => `<h2>${esc(title)}${extra}</h2>${lead ? `<p class="g-lead">${gmd(lead)}</p>` : ""}`;
const gTable = (head, rows) => `<div class="panel tablewrap"><table class="g-table"><thead><tr>${head.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead>
  <tbody>${rows.join("")}</tbody></table></div>`;
const gCards = cards => `<div class="g-cards">${cards.map(([k, title, text]) =>
  `<div class="panel g-card"><div class="fk">${esc(k)}</div><b>${esc(title)}</b><p>${gmd(text)}</p></div>`).join("")}</div>`;
const gRules = rules => `<ol class="g-rules">${rules.map(([title, text]) =>
  `<li class="panel g-rule"><b>${gmd(title)}</b><p>${gmd(text)}</p></li>`).join("")}</ol>`;
const gKinds = C => `<ul class="g-kinds">${C.proof_kinds.map(k => `<li><span class="mono">${esc(k.kind)}</span> — ${esc(k.text)}</li>`).join("")}</ul>`;

/* One SVG figure. Markers are per figure (ids prefixed) so several diagrams can share the page. */
function gxFig(id, w, h, label, body) {
  const mk = (n, cls) => `<marker id="gx-${id}-${n}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9"
    markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0 1L10 5L0 9z" class="gx-ah ${cls}"/></marker>`;
  return `<figure class="panel g-diag"><div class="g-scroll"><svg class="g-svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(label)}">
    <defs>${mk("a", "")}${mk("b", "brand")}${mk("r", "bad")}</defs>${body}</svg></div></figure>`;
}
/* drawing kit bound to one figure id */
function gxKit(id) {
  return {
    node(x, y, w, h, title, sub = [], cls = "") {
      const lines = [].concat(sub).filter(Boolean), lh = 16;
      const top = y + h / 2 - lines.length * lh / 2;
      return `<g class="gx-node ${cls}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="8"/>
        <text class="gx-t" x="${x + w / 2}" y="${top}">${esc(title)}</text>
        ${lines.map((s, i) => `<text class="gx-s" x="${x + w / 2}" y="${top + (i + 1) * lh}">${esc(s)}</text>`).join("")}</g>`;
    },
    group(x, y, w, h, title) {
      return `<g class="gx-group"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/><text class="gx-gt" x="${x + 12}" y="${y + 18}">${esc(title)}</text></g>`;
    },
    edge(d, cls = "", m = "a", both = false) {
      const u = `url(#gx-${id}-${m})`;
      return `<path class="gx-e ${cls}" d="${d}" marker-end="${u}"${both ? ` marker-start="${u}"` : ""}/>`;
    },
    line(x1, y1, x2, y2, cls, m, both) { return this.edge(`M${x1} ${y1}L${x2} ${y2}`, cls, m, both); },
    label(x, y, text, cls = "") { return `<text class="gx-l ${cls}" x="${x}" y="${y}">${esc(text)}</text>`; },
  };
}

// ---------------------------------------------------------------- diagrams
function gxArch(L) {
  const k = gxKit("arch");
  const envs = [["repo", 76, ""], ["local", 76, ""], ["staging", 88, ""], [L.prod, 150, "k-warn"]];
  return gxFig("arch", 960, 470, L.egd, [
    k.node(20, 30, 180, 64, L.agents, [L.agentsSub]),
    k.node(20, 120, 180, 64, L.people, [L.peopleSub], "k-brand"),
    k.node(260, 30, 200, 64, L.cli, [L.cliSub]),
    k.node(260, 120, 200, 64, L.console, [L.consoleSub]),
    k.line(200, 62, 260, 62), k.line(200, 152, 260, 152), k.line(200, 136, 260, 82),
    k.group(520, 14, 220, 252, L.egd),
    k.node(536, 44, 188, 50, L.plan, [L.planSub]),
    k.node(536, 104, 188, 50, L.docs, [L.docsSub]),
    k.node(536, 164, 188, 50, L.events, [L.eventsSub], "k-brand"),
    k.label(630, 246, L.committed, "muted"),
    k.line(460, 62, 520, 62), k.label(490, 52, L.writes),
    k.line(460, 152, 520, 152), k.label(490, 142, L.writes),
    k.node(800, 60, 140, 64, L.engine, [L.engineSub]),
    k.line(740, 120, 800, 98), k.label(770, 96, L.reads),
    k.node(800, 160, 140, 64, L.gates, [L.gatesSub], "k-brand"),
    k.line(870, 124, 870, 160),
    k.label(870, 246, L.shown, "muted"),
    k.node(520, 300, 220, 56, L.proofs, [L.proofsSub]),
    k.line(630, 300, 630, 266), k.label(672, 286, L.recorded),
    k.line(630, 356, 630, 404), k.label(680, 383, L.envs),
    envs.map(([n, w, c], i) => k.node(520 + envs.slice(0, i).reduce((a, e) => a + e[1] + 10, 0), 404, w, 44, n, [], c)).join(""),
    k.node(20, 300, 440, 70, L.multi, [L.multi1, L.multi2]),
    k.line(300, 184, 300, 300, "dash"),
    k.node(20, 386, 440, 62, L.dash, [L.dash1]),
  ].join(""));
}

function gxGates(L) {
  const k = gxKit("gates"), x = i => 11 + i * 138, cx = i => x(i) + 55;
  return gxFig("gates", 960, 300, L.title, [
    G_GATES.map((g, i) => k.node(x(i), 110, 110, 64, g, [L.short[g]], g === "accept" ? "k-warn" : "")).join(""),
    G_GATES.slice(1).map((g, i) => k.line(x(i) + 110, 142, x(i + 1), 142)).join(""),
    k.edge(`M${cx(6)} 110C${cx(6)} 44 ${cx(1)} 44 ${cx(1)} 110`, "cr", "r"),
    k.label((cx(1) + cx(6)) / 2, 40, L.cr, "bad"),
    k.label(cx(1), 194, L.clarifyNote, "muted"),
    k.label(cx(3), 194, L.sliceNote, "muted"),
    k.node(cx(5) - 65, 214, 130, 44, L.uat, [L.uatSub], "k-warn"),
    k.line(cx(5), 214, cx(5), 174),
    k.label(11, 288, L.lite, "lft muted"),
  ].join(""));
}

function gxFresh(L) {
  const k = gxKit("fresh");
  return gxFig("fresh", 960, 275, L.title, [
    k.node(20, 40, 170, 70, L.run, [L.runSub]),
    k.node(250, 40, 220, 70, L.rec, [L.rec1, L.rec2]),
    k.node(570, 40, 170, 70, L.fresh, [L.freshSub], "s-done"),
    k.line(190, 75, 250, 75), k.line(470, 75, 570, 75), k.label(520, 66, L.same),
    k.line(360, 110, 360, 150),
    k.node(250, 150, 220, 70, L.change, [L.changeSub]),
    k.node(570, 150, 170, 70, L.stale, [L.staleSub], "k-warn"),
    k.node(790, 150, 150, 70, L.closed, [L.closedSub], "k-bad"),
    k.line(470, 185, 570, 185), k.line(740, 185, 790, 185),
    k.edge("M865 220L865 250L105 250L105 110", "brandline", "b"),
    k.label(485, 250, L.rerun, "brand"),
  ].join(""));
}

function gxInbox(L) {
  const k = gxKit("inbox");
  const pill = (y, text, cls) => `<g class="gx-node ${cls}"><rect x="220" y="${y}" width="210" height="28" rx="14"/><text class="gx-s" x="325" y="${y + 14}">${esc(text)}</text></g>`;
  return gxFig("inbox", 960, 225, L.title, [
    `<g class="gx-stack"><rect x="28" y="68" width="142" height="70" rx="8"/><rect x="24" y="64" width="142" height="70" rx="8"/></g>`,
    k.node(20, 60, 142, 70, L.repos, [L.reposSub]),
    k.group(205, 26, 240, 148, t("Inbox")),
    pill(56, `${t("Decide")} — ${L.decide}`, "k-brand"), pill(94, `${t("Unblock")} — ${L.unblock}`, "k-warn"), pill(132, `${t("Hygiene")} — ${L.hygiene}`, ""),
    k.node(480, 50, 200, 100, L.action, [L.action1, L.action2, L.action3], "k-brand"),
    k.node(715, 60, 120, 80, L.event, [L.event1, L.event2]),
    k.node(865, 60, 80, 80, L.commit, [L.commitSub]),
    k.line(170, 100, 205, 100), k.line(445, 100, 480, 100), k.line(680, 100, 715, 100), k.line(835, 100, 865, 100),
    k.label(20, 208, L.ro, "lft muted"),
  ].join(""));
}

function gxTask(L) {
  const k = gxKit("task"), S = L.states;
  return gxFig("task", 960, 335, L.title, [
    k.node(40, 160, 140, 52, S.todo, [], "s-todo"),
    k.node(280, 160, 140, 52, S.doing, [], "s-doing"),
    k.node(520, 160, 140, 52, S.review, [], "s-review"),
    k.node(760, 160, 140, 52, S.done, [], "s-done"),
    k.line(180, 186, 280, 186), k.label(230, 177, "start", "mono"),
    k.line(420, 186, 520, 186), k.label(470, 177, "submit", "mono"),
    k.line(660, 186, 760, 186), k.label(710, 177, "accept", "mono"),
    k.edge("M600 160C600 110 400 110 400 160"), k.label(500, 140, L.reject, "mono"),
    k.edge("M790 160C790 60 360 60 360 160"), k.label(575, 104, L.reopen, "mono"),
    k.edge("M300 160C300 0 870 0 870 160", "dash"), k.label(585, 64, L.solo),
    k.node(40, 268, 620, 50, S.blocked, [L.blockSub], "s-blocked"),
    [110, 350, 590].map(x => k.line(x, 212, x, 268, "dash badline", "r", true)).join(""),
  ].join(""));
}

// ---------------------------------------------------------------- tabs
function gOverview(T, C) {
  const O = T.overview, tiers = C?.tiers || G_TIERS;
  return `<div class="g-hero"><div class="fk">${esc(O.kicker)}</div><h1>${esc(O.title)}</h1><p>${gmd(O.lede)}</p></div>
    ${gCards(O.cards)}
    ${gSec(O.rulesTitle)}${gRules(O.rules)}
    ${gSec(O.tiersTitle)}
    ${gTable(O.tiersHead, ["lite", "standard", "full"].map(t => `<tr><td><b class="mono">${t}</b></td>
      <td class="wrap">${esc(O.tierFor[t])}</td><td class="wrap">${gChips(tiers[t] || [])}</td></tr>`))}`;
}

function gArch(T) {
  const A = T.arch;
  const tree = A.tree.map(([p, note]) => esc(p) + (note ? `<span class="muted">${" ".repeat(Math.max(2, 26 - p.length))}${esc(note)}</span>` : "")).join("\n");
  return `${gSec(A.title, A.lead)}${gxArch(A.d)}
    ${gSec(A.whereTitle)}
    ${gTable(A.whereHead, A.where.map(([p, what, c]) => `<tr><td class="mono">${esc(p)}</td><td class="wrap">${gmd(what)}</td>
      <td>${c ? st("ok", A.yes) : st("neutral", A.no)}</td></tr>`))}
    ${gSec(A.treeTitle)}<pre class="map g-tree">${tree}</pre>`;
}

function gFlows(T) {
  const F = T.flows;
  return `${gSec(F.a.title, F.a.lead)}${gxGates(F.a)}
    ${gSec(F.b.title, F.b.lead)}${gxFresh(F.b)}
    ${gSec(F.c.title, F.c.lead)}${gxInbox(F.c)}`;
}

function gTask(T, C) {
  const K = T.task;
  return `${gSec(K.title, K.lead)}${gxTask(K)}
    ${gSec(K.transTitle)}
    ${C ? gTable(K.transHead, C.transitions.map(x => `<tr><td class="mono">egd ${esc(x.cmd)}</td><td>${esc(x.from)} → ${esc(x.to)}</td>
      <td>${gChips(x.roles)}</td><td class="wrap">${esc(x.checks)}</td></tr>`)) : gNoCore(T, C)}`;
}

function gGates(T, C) {
  const G = T.gates;
  if (!C) return `${gSec(G.title)}${gNoCore(T, C)}`;
  const saved = store.get("coreTier", "standard"), tier = C.tiers[saved] ? saved : "standard";
  const inTier = g => C.tiers[tier].includes(g);
  const feats = allFeatures();
  const isEgd = x => (P.repos.find(r => r.id === x.repo)?.source || "egd") === "egd";
  const pick = feats.find(x => isEgd(x) && x.gate !== "released") || feats.find(isEgd) || feats[0];
  const fsel = store.get("coreFeature", pick ? pick.repo + "/" + pick.slug : "");
  const f = feats.find(x => x.repo + "/" + x.slug === fsel) || pick;
  const tierSel = UI.select("coreTier", tier, ["lite", "standard", "full"].map(v => ({ value: v, label: v, hint: G.tierHint[v] })),
    v => { store.set("coreTier", v); render(); }, { prefix: G.tier });
  const featSel = feats.length ? UI.select("coreFeature", f ? f.repo + "/" + f.slug : "", feats.map(x => ({ value: x.repo + "/" + x.slug,
    label: x.title, hint: x.repo })), v => { store.set("coreFeature", v); render(); }, { prefix: G.feature }) : "";
  const cards = C.gates.map((g, i) => `
    <div class="panel gatecard ${inTier(g.name) ? "" : "skipped"}">
      <div class="gn"><span class="gi">${i + 1}</span><b>${esc(g.name)}</b>${inTier(g.name) ? "" : `<span class="chip">${esc(G.notIn.replace("{tier}", tier))}</span>`}</div>
      <div class="gp">${esc(g.proves)}</div>
      <ul class="rules">${g.rules.map(r => {
        const on = r.tiers.includes(tier) && inTier(g.name);
        return `<li class="${on ? "" : "off"}">${on ? "✓" : "–"} ${esc(r.text)}${r.tiers.length < 3 ? ` <span class="chip">${esc(r.tiers.join(" · "))}</span>` : ""}</li>`; }).join("")}</ul>
      ${g.records ? `<div class="grec">${esc(g.records)}</div>` : ""}
      <div class="gs muted small">${esc(G.signedBy)} ${gChips(g.signs)}</div>
    </div>`).join("");
  const live = f ? `<div class="panel live">
      <div class="lgates">${f.gates.map(g => `<div class="lg ${g.passed ? "done" : g.name === f.gate ? "next" : ""}">
        <div class="ln">${g.passed ? "✓" : g.name === f.gate ? "▶" : "·"} ${esc(g.name)}</div>
        <div class="muted small">${g.passed ? `${esc(g.by || "")}${g.at ? " · " + ago(g.at) : ""}` : g.name === f.gate ? esc(G.next) : ""}</div></div>`).join("")}</div>
      ${f.gate === "released" ? `<p>${esc(G.released)}</p>` : `<div class="callout ${f.next_problems.length ? "" : "ready"} mt-0">
        <b>${esc(f.gate)}</b> ${f.next_problems.length ? `${esc(G.closed)}<ul>${f.next_problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul>` : esc(G.open)}</div>`}
      <a class="btn sm" href="#/r/${encodeURIComponent(f.repo)}/f/${encodeURIComponent(f.slug)}">${esc(G.openFeature)}</a></div>`
    : `<div class="panel empty">${esc(G.none)}</div>`;
  return `${gSec(G.title, G.lead, tierSel)}<div class="pipeline">${cards}</div>
    ${gSec(G.trustTitle)}<div class="g-grid trust">${C.trust.map(t => `<div class="panel tcard"><b>${esc(t.title)}</b><p>${esc(t.text)}</p></div>`).join("")}
      <div class="panel tcard"><b>${esc(G.kinds)}</b>${gKinds(C)}</div></div>
    ${gSec(G.liveTitle, "", featSel)}${live}`;
}

function gRoles(T, C) {
  const R = T.roles, roles = C?.roles || {};
  const rows = Object.entries(roles).map(([action, rs]) => `<tr><td class="mono">${esc(action)}</td><td>${gChips(rs)}</td><td class="wrap">${esc(R.what[action] || "")}</td></tr>`);
  rows.push(`<tr><td class="mono">work</td><td>${gChips([R.anyMember])}</td><td class="wrap">${esc(R.what.work)}</td></tr>`);
  rows.push(`<tr><td class="mono">${esc(R.agents)}</td><td>${gChips(["agent"])}</td><td class="wrap">${esc(R.what.agent)}</td></tr>`);
  return `${gSec(R.title, R.lead)}${gTable(R.head, rows)}
    <div class="g-split">
      <div>${gSec(R.teamTitle, R.teamLead)}<pre class="map g-tree">${esc(TEAM_EXAMPLE)}</pre></div>
      <div>${gSec(R.signerTitle)}<div class="panel g-card">${R.signer.map(p => `<p>${gmd(p)}</p>`).join("")}</div></div>
    </div>
    ${gSec(R.sodTitle)}${gRules(R.sod)}`;
}

function gGlossary(T, C) {
  const Gl = T.glossary;
  const kinds = C ? gKinds(C) : `<p class="mono">test · http · cli · ui</p>`;
  return `${gSec(Gl.title, Gl.lead)}<dl class="panel g-dl">${Gl.terms.map(([term, text], i) =>
    `<dt>${esc(term)}</dt><dd>${gmd(text)}${i === 6 ? kinds : ""}</dd>`).join("")}</dl>`;
}

// ---------------------------------------------------------------- live rules
// The rules come from coreFor() in console.html, shared with each feature's Gates tab: undefined while
// they load, null when a live page could not fetch them (a retry button) or a snapshot lacks them.
const guideCore = lang => coreFor(lang);
const gNoCore = (T, C) => `<div class="panel empty">${C === undefined ? `<span aria-busy="true">${esc(t("Loading…"))}</span>`
  : LIVE ? `${esc(T.coreFailed)} <button type="button" class="btn sm" data-retry>${esc(T.retry)}</button>` : esc(T.noCore)}</div>`;

// ---------------------------------------------------------------- page
function guidePage() {
  const lang = getLang(), C = guideCore(lang);
  const T = GUIDE[lang] || GUIDE.en;
  const saved = store.get("guideTab", "overview"), cur = G_TABS.includes(saved) ? saved : "overview";
  const body = { overview: gOverview, arch: gArch, flows: gFlows, task: gTask, gates: gGates, roles: gRoles, glossary: gGlossary }[cur](T, C);
  return `<div class="page guide">
    <div class="g-tabs" role="tablist" aria-label="${esc(T.tablist)}">${G_TABS.map(id => `<button type="button" role="tab" id="gt-${id}" class="g-tab"
      data-gtab="${id}" aria-selected="${id === cur}" aria-controls="gp-${id}" tabindex="${id === cur ? 0 : -1}">${esc(T.tabs[id])}</button>`).join("")}</div>
    <section role="tabpanel" id="gp-${cur}" aria-labelledby="gt-${cur}">${body}</section>
  </div>`;
}

/* tab switching: delegated once, because the page is re-rendered as a string */
function guideGo(id) {
  store.set("guideTab", id); render();
  const main = document.getElementById("main"); if (main) main.scrollTop = 0;
  const tab = document.getElementById("gt-" + id);
  if (tab) { tab.focus(); tab.scrollIntoView({ block: "nearest", inline: "nearest" }); }
}
document.addEventListener("click", e => {
  const b = e.target.closest?.("[data-gtab]");
  if (b && b.getAttribute("aria-selected") !== "true") guideGo(b.dataset.gtab);
});
document.addEventListener("keydown", e => {
  const b = e.target.closest?.("[data-gtab]"); if (!b) return;
  const i = G_TABS.indexOf(b.dataset.gtab);
  const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: G_TABS.length - 1 }[e.key];
  if (to == null) return;
  e.preventDefault();
  guideGo(G_TABS[(to + G_TABS.length) % G_TABS.length]);
});
