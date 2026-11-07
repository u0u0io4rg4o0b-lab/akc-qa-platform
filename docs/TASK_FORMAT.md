# 測自己的專案

參考 `examples/discount/qa-task.json`。工作檔與計畫可放在本工具的 `work/`；結果放 `runs/`，兩者均不進 Git。目標專案不需要搬到本工具內。

```sh
python3 qa.py plan work/qa-task.json --project-root "/你的/個人專案" --out work/plan-001.json
python3 qa.py run work/plan-001.json --approve "上一步完整的plan_id" --out runs/attempt-001
python3 qa.py verify runs/attempt-001
```

Windows 以 `py -3` 取代 `python3`。`plan` 不啟動受測程式；它讀取明列檔案並計算身分。它會固定本機絕對路徑，因此搬移目標、工具變更、環境變更或檔案變更後要建立新計畫。已產生的證據包可以搬移後核對，不需要原專案仍存在。

| 欄位 | 內容 |
|---|---|
| schema_version | 整數 1 |
| project | name、version，指定本次受測對象 |
| scope | included、excluded 字串陣列 |
| files | 目標專案內相對路徑，含需求、測具、產品、鎖檔；最多 256 個一般檔案，每個最多 4 MiB，拒絕 symlink/hardlink |
| requirements | id、text、source；正解來源不能只是「目前實作如此」 |
| checks | 1–64 項，每項有 id、requirement_id、description、argv、timeout_seconds、expected_exit、mode |
| expected_json | json_equals 的完整預期 JSON，可為物件、陣列或值 |
| expected_stderr | 可省略，預設空字串；如確定需要，事前列完整 UTF-8 預期 stderr，逐位元組比對，不能填通用忽略規則 |

`argv` 是參數陣列，不是 shell 字串。支援 `{python}`（目前 Python）及 `{project}`（受測根目錄）替換；cwd 為受測根目錄。最長每項 120 秒；stdin 關閉；每個輸出流最多保存 1 MiB。環境變數繼承目前程序，**不記錄變數值但也不會替子命令阻擋憑證**。可執行檔被固定並計算 hash；套件、子程序及其他未列檔案不會自動全量固定。依賴完整性保持受限。

模式：

- `json_equals`：stdout 需為完整 UTF-8 JSON、沒有重複欄位／額外輸出，與 expected_json 精確相等；`true` 不等於 `1`。預期退出碼與 expected_stderr 也必須相符，才是本項 PASS。範例的退出碼 2 是事前定義的拒絕情境。
- `exit_code`：只比對退出碼與 expected_stderr，成功為 `EXIT_MATCH`；整體顯示 `COMMANDS_COMPLETED_QA_UNCONFIRMED`，不能把空測試套件的 rc 0 變成 QA PASS。報告顯示實際退出碼與各流末六行／最多 600 字，但不從文字猜出「34 個案例」的可靠分母。接 pytest／其他測試套件時，Agent 必須另核原始案例集合、跳過項與報告；本工具沒有內建 JUnit／pytest parser。

非空 stderr 預設仍失敗，原因為 `STDERR_MISMATCH`，表示計畫不符，不能單憑它宣稱產品漏洞。先讀原文分辨警告與錯誤。`expected_stderr` 必須在執行前寫進計畫；任何多出的訊息仍會失敗。換行也逐位元組比對，Windows 子程式可能輸出 `\r\n`。含變動 PID／時間的警告不能直接套固定字串。

Node 可先核對自己的 `node --version`／`node --help`，若該版本支援，再於計畫命令明列特定的 `--disable-warning=ExperimentalWarning`（或具名 warning code），並說明選擇。這會減少該類警告的可見性，不能因此視實驗功能穩定。不要預設加 `--no-warnings` 隱藏全部警告；未知的 stderr 也不自動忽略。相關官方資訊與採納邊界見回饋審查。

若專案已有測試，可接既有可信命令；若需要精確行為 PASS，可寫小測具呼叫真正產品並輸出結果 JSON。固定測具與需求，先確認空執行、刻意錯誤或錯案例不會假綠。這並不建立獨立 oracle 或獨立見證。

結果資料夾包含 plan.json、attempt.json、逐項 native.json／stdout.bin／stderr.bin、result.json、REPORT.md、manifest.json。完整收據才可 verify；中斷後沒有完整 manifest 的目錄屬未完成，不能把目錄存在當通過。核對工具會重算判讀與案例分母；使用者仍可同時偽造整包，hash 不提供外部可信性。

preview.2 的 result schema 為 2。native.json 含 assessment（verdict、reason_codes）；摘要與報告同時重算原因、退出碼及輸出預覽。報告分列未配置檢查、所列檢查全部 NOT_RUN，以及所列檢查尚未全部取得行為 PASS 的需求；這些都只以本計畫的案例為分母。

可讀回 preview.1 的舊 schema／舊報告。僅對該舊版，允許完全符合原報告內容的 LF 或已知 CRLF 寫入格式，且先驗原 manifest；不改寫舊包、不忽略其他內容差異。新包一律使用 UTF-8／LF 位元組。舊計畫不能直接執行，請以新版重建並核對新 plan_id。

FAIL 表示已完成觀察違反指定判準；格式、逾時、截斷、執行／來源變更等為 INCONCLUSIVE；前項失敗後為 NOT_RUN。CLI：成功／純命令完成為 0，FAIL／INCONCLUSIVE 為 1，無法准入或證據核對失敗為 2。輸出限制、逾時及取消只提供盡力停止，detached child 與回滾未知，不可自動重送。

Windows 子程序使用 CREATE_NO_WINDOW 避免另開 console；這不是完整 GUI 顯示或程序樹隔離。Windows 清理仍為父程序層級，孫程序可能存活；遇到此類任務應先準備自己的停止／清理方式，不宣稱完整清理。工具自測可在慢機器用 `AKC_QA_TEST_TIMEOUT_SCALE=2`（1–10）調整測具預算；例如 PowerShell `$env:AKC_QA_TEST_TIMEOUT_SCALE='2'`，再跑 `py -3 -B -m unittest discover -s tests -v`。此設定不更改你的正式 QA 計畫，也不是極端忙碌環境必定通過的保證。
