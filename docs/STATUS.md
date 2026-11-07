# 版本與證據範圍

工具版本：`0.4.0-preview.2`；目前授權：`LicenseRef-AKC-Product-QA-2.0`。允許商業產品與企業內部 QA，兩類對外營利 QA 服務／平台再商業化另需授權，見 LICENSE 與 [政策說明](LICENSE_POLICY_20261108.md)。

可用：本機計畫、直接命令執行、JSON 判讀、命令退出狀態、原始輸出、報告與離線核對。只使用 Python 3.11+ 標準函式庫。原始版本歷史與私人收據不包含在此公開包。

Windows 修正輪的歷史精確環境、次數及結果見 [RELEASE_VALIDATION.json](RELEASE_VALIDATION.json)。本次授權後繼的來源變更與回歸界線另見 [LICENSE_RELEASE_VALIDATION.json](LICENSE_RELEASE_VALIDATION.json)，不能把舊提交的原生測試改稱新提交已原生重跑。工具自測與五項樣例分開計數，樣例不是實際商業專案的 QA。

| 範圍 | 狀態／界線 |
|---|---|
| 可信本機命令 | 已實作；企業與商業產品可依法授權使用，啟動與核對結果依發布驗證記錄 |
| Codex／Claude 入口 | 已收到朋友轉述的 Windows 11／Python 3.11.9／Claude Code 試用回饋；原始收據未提供。這是舊版單行修補後的自述，不冒充新版真人回測或獨立審計 |
| Windows／Linux／macOS | Windows 修正輪的精確原生結果及 CI 提交見 RELEASE_VALIDATION.json；本次授權輪未重跑矩陣，CI 主機也不等同朋友 Windows 11 桌面 |
| 瀏覽器／DB／遠端 API | 無內建整合；可由另行準備的測具觀察，結論依實際環境 |
| 隔離 runner／封網／獨立證據保管 | 不提供 |
| 安全審計／正式產品驗收 | 未完成，不能由工具自測升格 |
| 自動影響分析／自動回歸沿用 | 不提供；Agent 應根據實際變更規劃並說明理由 |

下一個產品 Gate 是具名合法真實商業專案的使用價值驗證：需求 → 計畫 → 真測試 → 報告 → 核對與失敗讀回，並和既有流程比較實際增量價值。見 [Gate E](COMMERCIAL_PILOT.md)。尚未指定試點，沒有因授權變更就取得第三方系統存取、對外合作或付費服務承諾。

[逐項回饋、研究與採納判斷](FEEDBACK_REVIEW_20261108.md)。Windows 完整程序樹清理仍是已明示限制；視為另個有實測需求的 Gate，不由本輪換行與報告修正升格成已解決。

11/8 原生 CI 已取得 Windows 31通過、macOS／Linux各30通過＋1個Windows專屬跳過，三者各自五項樣例及證據核對通過。第一輪 Linux 的安裝包準備失敗仍保留；只在CI環境後繼重跑Linux，工具／測具／驗證器來源相同。各次提交、完整分母與未證範圍見發布驗證，不相加成產品成功率。
