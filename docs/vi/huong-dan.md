# EGD — Hướng dẫn cho team

EGD (Evidence-Gated Delivery) là quy trình giao hàng phần mềm **có cổng kiểm soát bằng
bằng chứng**. Kế hoạch nằm trong file, tiến độ nằm trong nhật ký sự kiện, và mọi cổng
(gate) đều do CLI `egd` tính toán — không ai (người hay AI) "nói khéo" để qua được.

## 1. Bảy cổng

| Cổng | Trả lời câu hỏi | Ai ký |
|---|---|---|
| `frame` | Vì sao làm? (brief + bản đồ repo đã được người xác nhận) | PM / Lead |
| `clarify` | Làm chính xác cái gì? (giả định + AC dạng Given/When/Then) | PM / Lead |
| `design` | Làm thế nào? (cách tiếp cận, phương án bị loại, lỗi có thể gặp) | PM / Lead |
| `slice` | Theo thứ tự nào? (slice demo được, task ≤ 4h, đồ thị phụ thuộc hợp lệ) | PM / Lead |
| `build` | Đã chạy đúng chưa? (task xong qua review, proof pass trên code hiện tại) | PM / Lead |
| `accept` | Khách đồng ý chưa? (UAT từng slice, không còn bug nghiêm trọng) | Khách / PO |
| `release` | Ship được và rollback được chưa? | PM / Lead |

**Tier** chọn theo quy mô:
- `lite`: bugfix hoặc việc dưới 1 ngày, bỏ qua `design` và `accept`.
- `standard`: đi đủ 7 cổng.
- `full`: dự án fixed-price hoặc rủi ro cao. Bắt buộc mỗi AC có proof, mỗi task có test.

## 2. Thiết lập lần đầu

Cài một lần cho máy (lệnh `egd` và plugin Claude Code nếu máy có Claude Code):

```bash
curl -fsSL https://raw.githubusercontent.com/LocTran12310/egd/main/install.sh | sh
```

Chạy trên macOS và Linux; trên Windows thì dùng WSL. Cần git; uv tự mang Python theo (pipx cần
Python 3.11+).

Cập nhật: `egd update`. Gỡ: `egd uninstall` — liệt kê trước những gì sẽ gỡ (CLI, plugin, cài đặt
và dịch vụ chạy nền của console, Playwright trong `~/.egd/ui`, phần Docker còn sót) rồi hỏi xác
nhận. Giữ lại: `.egd/` của mọi repo (kể cả phiên đăng nhập trong `.egd/.auth/`) và bộ nhớ đệm
trình duyệt dùng chung của Playwright (`ms-playwright`).

Rồi trong mỗi repo:

```bash
egd setup
```

Sau đó:
1. Điền `.egd/team.toml`: tên, GitHub handle, `aliases` (tên khác của cùng người, ví dụ `git config user.name`) và vai trò (`pm`, `lead`, `dev`, `qa`, `client`, `po`).
2. Điền `.egd/config.toml`: lệnh test, các môi trường (local, staging, prod) và repo GitHub.
3. Để AI (agent `egd-scout`) vẽ `.egd/map.md`. **Một người** đọc lại rồi ghi tên vào `reviewed_by:`.
4. Commit thư mục `.egd/`. Mỗi sự kiện là một file riêng nên không bao giờ conflict khi merge.

**Stack profile.** `egd setup` tự nhận ra stack (python, node, react, go) và đặt sẵn lệnh chạy test. `egd profile` cho xem quy ước của stack, danh sách cần soi khi review, và các mục done_when mà task chạm vào loại file đó nên có (`egd add task` sẽ nhắc). Muốn chỉnh riêng cho một repo: `egd profile --new shop --from react` (lưu ở `.egd/profiles/`, commit để cả team dùng chung); muốn dùng cho mọi repo của bạn: thêm `--mine` (lưu ở `~/.egd/profiles/`). Profile của repo được ưu tiên, rồi tới của bạn, cuối cùng là profile có sẵn. Chọn profile cho repo: `egd profile --use react,node`.

**Ai ký?** Lệnh nào ghi lại chữ ký (`new`, `pass`, `start`, `submit`, `accept`, `uat`, `proof run`, `add`, `set`, …) đều nhận `--by TÊN`. Bỏ `--by` thì EGD ký bằng tên của bạn: `$EGD_USER`, nếu không có thì `git config user.name`. Khi `team.toml` đã có thành viên, tên đó được so với tên, GitHub handle và `aliases` của từng người rồi ghi bằng tên chuẩn của thành viên; không khớp ai thì lệnh bị từ chối kèm danh sách thành viên và cách sửa. Khi tên lấy mặc định, EGD luôn in một dòng `signed as <tên> (from …)` để không ai ký nhầm. Muốn ghi thay người khác (ví dụ khách xác nhận UAT qua điện thoại) thì vẫn dùng `--by`. Bên trong Claude Code, agent luôn phải truyền `--by`: CLI từ chối mọi chữ ký ngầm định ở đó. Các ví dụ dưới đây ghi rõ `--by` để thấy ai ký việc gì.

Gõ `egd` không kèm gì để xem nhanh từng feature đang ở đâu và lệnh nên chạy tiếp theo; `egd -h` liệt kê lệnh theo nhóm; lệnh nào làm việc với một feature cũng nhận `-f <feature>` (hoặc chỉ cần gõ tên — `egd <feature>` là xem trạng thái feature đó; repo chỉ có một feature thì không cần); gõ sai tên lệnh hoặc tên cổng sẽ được gợi ý "did you mean …?".

## 3. Feature đầu tiên trong 5 phút

Một feature `lite`, từ con số 0 đến lúc release, trong một repo git. Bạn ký bằng tên của mình
(`git config user.name`); một đồng đội accept task của bạn.

```bash
egd setup                     # .egd/ — rồi đặt test_command = "pytest {tests}" trong [proof]
                              # của .egd/config.toml
egd new checkout --tier lite  # rồi viết brief.md: Problem, Outcome, Success signal, Out of scope
egd pass frame
egd add ac --given "giỏ có 2 × A1 giá 150.000" --when "người mua thanh toán" --then "tổng là 300.000"
egd pass clarify
egd add slice --title "Thanh toán hiện tổng tiền" --covers AC-1.1 --demo "thanh toán, thấy 300.000"
egd add task --slice S-1 --title "Tính tổng" --estimate 2 --touches 'src/cart/**' \
    --tests tests/test_cart.py --done "tổng là 300.000"
egd add proof --kind test --verifies AC-1.1 --title "Tổng giỏ hàng" --tests tests/test_cart.py
egd pass slice
egd start T-1.1               # viết code; commit có "T-1.1" trong message
egd submit T-1.1 --confirm    # chạy test của task, kiểm tra chỉ sửa file trong touches
egd accept T-1.1 --by Bob     # đồng đội review — không bao giờ là người đã submit
egd verify --run              # commit code trước; lượt cuối: test, proof, mọi gate phía trước
egd pass build                # rồi commit trail nếu .egd/ được chia sẻ (không bao giờ khi để local)
egd pass release              # khi release.md đã ghi cách rollback
```

Mục nào ghi sai thì gỡ bằng `egd rm <id>` (tự do cho đến `slice`; sau đó phải qua change
request). `egd add <kind> -h` liệt kê option của từng loại kèm ví dụ; `egd check` cho biết vì
sao gate đang đóng và lệnh để sửa. Tier `standard` có thêm `design` và `accept` của khách.

## 4. Vòng đời một feature

```bash
egd new thanh-toan --tier standard            # ký bằng tên bạn (EGD_USER hoặc git user.name)
egd status                      # luôn chạy lệnh này trước: cổng tiếp theo và lý do đang đóng
```

- **frame**: viết `brief.md` gồm Vấn đề, Kết quả, Tín hiệu thành công và Ngoài phạm vi.
- **clarify**: điền `[[assumption]]` và `[[ac]]` trong `plan.toml`. Gom câu hỏi cho khách thành **một lượt, tối đa 7 câu**. Câu nào chưa có câu trả lời thì thành giả định.
- **design**: viết `design.md`, rồi chốt các `[[decision]]`.
- **slice**: chia `[[slice]]` (có kịch bản demo) và `[[task]]` (≤ 4h, có `touches`, `tests`, `done_when`). Chạy `egd graph` đến khi sạch.

Không quen TOML? Soạn plan bằng lệnh (mã được tự sinh, file luôn được kiểm tra hợp lệ trước khi ghi):

```bash
egd add assumption --by Linh --text "Giá chỉ tính VND" --blocking
egd add ac --by Linh --group 1 --given "giỏ có 2 × A1" --when "thanh toán" --then "đơn chờ xử lý, tổng 300.000" --levels unit,e2e
egd add slice --by Linh --title "Thanh toán tạo đơn" --covers AC-1.1 --demo "1. … 2. …"
egd add task --by Linh --slice S-1 --title "POST /orders" --estimate 3 --touches "src/orders/**" --done "trả 201 kèm tổng"
egd add proof --by Linh --kind test --verifies AC-1.1 --title "Tổng đơn hàng" --tests tests/orders.test.ts
egd add proof --by Linh --kind ui --verifies AC-1.1 --title "Trang xác nhận hiện tổng" --path /orders/latest \
    --expect-visible "text=300.000" --viewports desktop,mobile   # ảnh chụp theo từng viewport
egd set A-1 status=confirmed "resolution=PO xác nhận trong buổi họp" --by Linh
egd rm A-2 --by Linh            # gỡ một mục ghi nhầm (sau slice: phải qua change request)
```

Mỗi cổng mở thì người có quyền ký: `egd pass <cổng>` (ký bằng tên mình; `--by <tên>` khi ghi thay người khác).

## 5. Làm task (dev)

```bash
egd ready                                   # việc có thể nhận ngay
egd start T-1.1 --by An
# code trong phạm vi touches, commit message PHẢI có mã task:
git commit -m "T-1.1 tính tổng đơn hàng phía server"
egd submit T-1.1 --by An --confirm --spent 2.5
```

Lệnh `submit` sẽ:
- chạy test của task,
- kiểm tra không sửa file ngoài `touches` (scope drift),
- ghi nhận rằng bạn xác nhận danh sách `done_when`.

**Người khác** accept: `egd accept T-1.1 --by Linh`, hoặc trả lại bằng `egd reject ... --reason`.

Nếu làm một mình: `egd solo T-1.1 --by An --confirm --reason "..."`. Việc bỏ qua review được ghi lại công khai.

Đang chờ việc gì đó: `egd block T-1.1 --by An --reason "chờ key sandbox thanh toán"`.

## 6. Bằng chứng (QA)

Khai báo proof trong `proof.toml`. Chọn kind theo loại việc:

| Loại việc | Kind | Bằng chứng |
|---|---|---|
| API, webhook | `http` | Bản ghi request/response kèm bảng assert |
| Migration, cron, queue, kiểm tra dữ liệu | `cli` | Lệnh, exit code và output |
| Logic có unit test | `test` | Output của test runner |
| Giao diện | `ui` | Screenshot từng viewport |

```bash
egd proof run --by QA          # chạy proof, ghi transcript vào runs/
egd proof list                 # trạng thái mới nhất; proof cũ sau khi code đổi bị đánh dấu "stale"
egd proof report               # bảng AC → bằng chứng
```

Mật khẩu và token đặt trong `.egd/secrets.env` (không commit), tham chiếu bằng `${TEN_BIEN}`. Transcript tự che chúng thành `***`.

## 7. Khách hàng, thay đổi phạm vi, bug

```bash
# Khách muốn đổi yêu cầu sau clarify → mở CR, khách duyệt mới qua được cổng tiếp theo
egd cr open --by Linh --title "Thêm dòng VAT" --reason "quy định mới" --hours 3
egd cr approve CR-001 --by "Acme PO"

# Bug
egd bug open --by QA --title "Tổng tiền sai khi qty > 1" --severity major --found-in uat --ac AC-1.1
egd bug fix BUG-001 --by An --task T-1.3

# UAT
egd uat S-1 --by "Acme PO" --pass
```

## 8. Theo dõi chung cả team

```bash
egd standup                     # mỗi người: hôm qua làm gì, đang giữ việc gì; hàng đợi review; việc bị chặn
egd serve                       # dashboard live (chỉ đọc) tại http://127.0.0.1:8770
egd site --out dist             # xuất dashboard tĩnh để deploy (cẩn thận: có dữ liệu dự án)
egd board --html --print        # board: ai đang làm gì, hàng đợi review, việc bị chặn, CR chờ khách
egd sync github --by Linh        # đẩy task thành GitHub Issues (nhãn egd:<trạng thái>)
egd report thanh-toan --lang vi # báo cáo tuần cho khách (đặt mặc định: [report] lang = "vi")
egd metrics                     # độ chính xác ước lượng, thời gian chờ review, rework, leakage
egd pr thanh-toan               # mô tả PR kèm bảng AC → bằng chứng, và mục Verification (đã chạy gì, ở đâu, commit nào; chỗ nào chưa kiểm)
egd verify thanh-toan --run --by Linh --gate release   # lượt kiểm tra cuối: plan, test, proof, mọi gate phía trước — không tự pass gate
```

Nhịp làm việc gợi ý:
- **Agent và model theo từng task**: `egd ready` và `egd graph` ghi agent và model cho mỗi task, ví dụ `→ builder · opus (risky: auth)`. Mặc định: Opus cho việc rủi ro (bảo mật, tiền, migration, đồng thời) hoặc task từng bị trả lại, Haiku cho việc nhỏ (≤ 1 giờ), còn lại Sonnet; task chỉ sửa test giao cho tester. Muốn chọn tay: `egd add task … --agent builder --model opus` hoặc `egd set T-1.2 model=haiku`; `agent = "person"` để dành cho người. Model mạnh hơn không có thêm quyền: gate, accept, UAT vẫn do người ký.
- **Đóng ticket không release**: `egd close <feature> --as dropped|research|superseded --reason "…"` khi việc dừng giữa chừng, chỉ là nghiên cứu (lý do ghi lại điều tìm được), hoặc đã có việc khác thay. Không gate nào được pass, task dở được ghi lại, feature rời khỏi inbox; `--undo` để mở lại. Trong console: nút **Đóng feature** ở đầu trang feature.
- **Standup**: `egd standup`. Xem theo thứ tự hàng đợi review, rồi việc bị chặn, rồi việc ready.
- **Cuối tuần**: gửi `egd report` cho khách, xem `egd metrics` để cải thiện ước lượng.

## 9. Console cho PM — mọi repo trên một màn hình

Cho PM, lead hay QA theo nhiều dự án cùng lúc: `egd console` (→ http://127.0.0.1:8780) đọc mọi
repo đã đăng ký và đưa việc cần người quyết lên đầu.

- **Inbox:** task chờ review, gate sẵn sàng pass, CR và UAT chờ khách; rồi việc đang kẹt, rồi phần vệ sinh (trail chưa commit, plan lỗi). Repo chỉ đọc (ai-dlc) nằm ở mục riêng.
- **Thao tác ngay tại chỗ:** accept, reject, pass gate, ghi UAT (đi theo kịch bản demo như một checklist), duyệt CR, submit hoặc kết thúc task — chính là lệnh CLI tương ứng, ký bằng tên bạn cho repo đó.
- **Board, Pickable, Features, People, Approval trail** trên mọi repo, và một trang cho từng repo: cấu hình, team và vai trò, map đã ký, cách nối với Claude Code.
- **Thân thiện và nhanh:** tìm bằng ⌘K, dùng tốt bằng bàn phím và trình đọc màn hình, tiếng Anh và tiếng Việt, sáng và tối; chỉ tải những gì trang cần.

| Bạn muốn | Chạy | |
|---|---|---|
| Console cho riêng bạn | `egd console` | mở http://127.0.0.1:8780, rồi Repositories → Add repository… |
| Team cùng mạng LAN xem được | `egd console --lan` | in ra link có token theo IP LAN của máy; khách chỉ xem, thao tác chỉ làm được trên máy bạn |
| …và thao tác được luôn | `egd console --lan --lan-write` | mọi thao tác đều ký bằng **tên bạn** — nên để mỗi người tự chạy console của mình |
| Luôn chạy, không cần mở terminal | `egd console --service` (thêm `--lan` để chia sẻ) | tự chạy khi đăng nhập, tự bật lại nếu bị tắt, ~35 MB; mở bằng link riêng — gõ `egd console` để lấy link; `--service off` để gỡ |
| Chạy trên một máy chủ chung | `EGD_USER="<tên>" docker compose up -d` trong bản clone repo egd | tuỳ chọn — chỉ khi máy đó vốn đã chạy Docker |

Không cần Docker: console có sẵn trong `egd` vừa cài. `egd console` chạy khi terminal còn mở; `--service` giao console cho trình quản lý dịch vụ có sẵn của hệ điều hành (launchd trên macOS, systemd trên Linux). Console ký bằng `git config user.name` của bạn (đổi bằng `egd console --user "<tên>"`; mỗi repo có thể ký tên riêng).

Phần còn lại (người ký theo từng repo, tab Gates, thêm repo bằng giao diện) có trong mục **Hướng dẫn** ngay trong console.

## 10. Claude Code: plugin, skill và chuyển từ ai-dlc

- **Cài plugin:** trình cài đặt tự làm; cài tay bằng `claude plugin marketplace add LocTran12310/egd && claude plugin install egd@egd`. Plugin cài ở phạm vi user, nên skill có mặt trong mọi project nhưng chỉ hoạt động ở repo có `.egd/`. Skill `egd` (lên plan và đi qua các gate), `egd-proof` (tự chọn loại bằng chứng hợp với từng AC rồi chạy: ảnh chụp ui cho thứ người dùng nhìn thấy, http/cli/test cho phần còn lại), `egd-team` (board, standup, CR, bug, UAT, báo cáo khách, metrics, đồng bộ GitHub); subagent `egd-scout` (vẽ map repo), `egd-builder`, `egd-tester`, `egd-reviewer`. `egd update` cập nhật cả CLI lẫn plugin; sau đó khởi động lại Claude Code.
- **Skill cho việc ship:** `egd-commit` (commit theo task: chỉ stage file của task, ghi mã task vào message, không bao giờ force-push), `egd-pr` (mô tả PR theo đúng template của repo, chỉ ghi những gì đã thật sự chạy; đính ảnh chụp ui vào PR qua Claude in Chrome), `egd-review` (review PR theo coding standards của team — khai báo ở `[review] standards` trong config, có thể là file hoặc link Notion — cùng map và AC của feature), `egd-verify` (lượt cuối trước khi ký gate: chạy `egd verify --run` rồi đối chiếu từng AC với code). Quy tắc riêng từng repo đặt trong `.egd/config.toml`: `[pr] base`, `title`, `ticket` và `[review] standards`.
- **Chia sẻ hay chỉ để trên máy:** mặc định `.egd/` được commit để cả team cùng thấy plan và trail. `egd setup --local` giữ nó trên máy mình (thêm `.egd/` vào `.git/info/exclude`, không sửa file nào của repo); gõ `egd` sẽ cảnh báo nếu git vẫn còn theo dõi file trong đó, kèm lệnh gỡ.
- **Bằng chứng ảnh chụp:** `egd proof setup` cài Playwright một lần cho cả máy (vào `~/.egd/ui`, kèm Chromium khoảng 150 MB trong bộ nhớ đệm dùng chung `ms-playwright`); mọi repo dùng chung, không cần cấu hình gì thêm. `egd uninstall` gỡ Playwright, giữ bộ nhớ đệm đó.
- **Proof ui tự đăng nhập:** khai `[[ui.login]]` (hoặc `[[env.<tên>.login]]`) trong `.egd/config.toml`, mật khẩu để trong `.egd/secrets.env`. Mỗi viewport tự đăng nhập trước khi chạy, không cần `egd proof login` bằng tay. `screenshot = "<selector>"` chụp riêng một phần tử.
- **Thay đổi local không tính cho feature:** file đã sửa sẵn trên máy trước `egd start` (một dòng `.gitignore`, cấu hình editor) mà không đụng tới thì không bị tính là drift; thay đổi chưa commit nằm ngoài `touches` của feature không làm bằng chứng bị stale. Repo không có test runner thì đặt `test_command = ""` để EGD thôi nhắc.
- **Bật plugin EGD cho cả team:** chạy `egd setup --claude` trong repo rồi commit `.claude/settings.json`. Ai mở repo bằng Claude Code cũng được đề nghị cài plugin (gồm skill `egd-proof` để kiểm chứng).
- **Chuyển repo từ ai-dlc sang EGD:** chạy `egd import aidlc` trong repo đó. Mỗi feature `.ai/` sẽ thành một feature EGD (assumption, AC, ADR thành decision, UoW thành slice, ticket thành task kèm checklist done-when). Lịch sử được chép sang trail với đúng người làm và thời điểm gốc. Lệnh chỉ đọc `.ai/`, không sửa nó, và không commit gì. Nếu đã import trước đó mà AC bị đọc sai, chạy `egd import aidlc --refresh` để đọc lại AC từ `.ai/`; trail giữ nguyên.

## 11. Chạy thử

Cách nhanh nhất để thử là làm theo mục 3 (feature đầu tiên trong 5 phút) trong một repo trống.

Chạy Console bằng Docker, không cần cài gì thêm — lệnh chạy trong bản clone của repo egd
(`git clone https://github.com/LocTran12310/egd && cd egd`), nơi có file compose:

```bash
EGD_USER="Tên bạn" docker compose up -d      # http://127.0.0.1:8780, duyệt được ~/Documents
```

## 12. Quy tắc vàng

1. Luôn chạy `egd status` trước khi làm gì.
2. Không bao giờ sửa tay thư mục `events/`.
3. Chỉ ký bằng tên của chính mình (mặc định là vậy; `--by` chỉ để ghi lại quyết định mà người khác đã đưa ra). AI không được ký thay người.
