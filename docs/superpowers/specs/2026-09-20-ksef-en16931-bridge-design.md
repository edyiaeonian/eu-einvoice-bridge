# EU E-Invoice Bridge 設計規格

**日期**:2026-09-20(v3,經兩輪技術審查後修訂)
**狀態**:設計已確認,待撰寫實作計畫
**背景研究**:見 `docs/BACKGROUND.md`

---

## 1. 專案目的

建立一個可實際運作的電子發票處理管線,把一份中立的發票資料同時轉換成**歐盟語意標準 EN16931(UBL 2.1 語法)**與**波蘭國家格式 FA(3)**,通過官方規則驗證後,實際送入波蘭 KSeF 2.0 沙箱並取回官方收據(UPO)。

### 核心命題

這個專案要展示的不是「串接了某國政府 API」,而是:

> **理解 CTC(continuous transaction control)模型下,歐盟語意標準與各國申報格式之間的落差,並用架構把這個落差隔離、顯性化。**

EN16931 與 FA(3) 並非一對一對應。若讓兩者直接互轉,程式碼會退化成大量特例判斷。本專案的設計主張是:**兩者皆從同一中立模型出發,落差被隔離在各自的輸出器內,清楚可見。**

### 定位

**技術作品集專案,不追求商業化。**商業可行性評估見 `docs/BACKGROUND.md` 第 2 節。

衡量標準不是市佔或營收,而是**能否在技術面試中逐層講出設計決策的理由**。

---

## 2. 關鍵技術約束:為何選波蘭

此決策直接決定專案範圍,故留在規格本體。

- **波蘭 KSeF ✅**:測試環境接受匿名資料與標準測試用假 NIP,可在無真實授權下取得 token,**不需波蘭法人或稅號**
- **匈牙利 NAV ❌**:需 Ügyfélkapu+ 與匈牙利稅號,測試環境僅供已登記納稅義務人使用,**無在地法人即無法存取**

調查細節見 `docs/BACKGROUND.md` 第 3 節。

---

## 3. 分階段交付與驗收標準

每階段結束都是**完整、可展示的成果**,而非半成品。

### 階段一:離線驗證管線

**內容**:模型 + UBL 輸出 + XSD + Schematron 驗證 + CLI 錯誤報告

**驗收標準**(全部滿足才算完成):

1. 從一份 JSON 輸入可產生 UBL 2.1 XML
2. 該 XML 通過 **UBL 2.1 XSD** 與 **EN16931 core Schematron** 兩層驗證
3. 模型可表達至少:標準稅率、零稅率、免稅、逆向課稅四種稅別,且免稅與逆向課稅強制要求免稅理由
4. 稅額彙總由明細推導,人工無法直接指定
5. 給定一份含多處錯誤的輸入,CLI **一次列出所有錯誤**,每項含規則 ID、BT 編號、位置與可讀訊息
6. 全部測試不需網路即可通過

### 階段二:雙格式與落差處理

**內容**:FA(3) 輸出器 + 四類欄位落差處理

**驗收標準**:

1. 同一份輸入可同時產生 UBL 與 FA(3) 兩種 XML
2. FA(3) 產出通過官方 FA(3) XSD
3. 第 3 類落差(EN16931 有、FA(3) 無)產生警告但不中斷,且警告列出具體欄位
4. 第 4 類落差(FA(3) 必填、模型無)在輸出階段即失敗,不產生無效檔案
5. 多幣別案例可完整走通,展示 BT-6 與 FA(3) 匯率欄位的對應

### 階段三:加密與送件

**內容**:`crypto` 模組 + KSeF 客戶端 + 送件取 UPO

**驗收標準**:

1. `crypto` 模組以固定測試向量驗證 AES-256-CBC 與 RSA-OAEP 實作正確
2. 可在沙箱完成認證、開 session、送出加密發票
3. 可輪詢狀態並下載 UPO,UPO 與 KSeF 編號寫入本地狀態檔
4. 程式中斷後重啟,可憑狀態檔的參考編號續查,不重送
5. `ksef` 模組的狀態機以 `respx` 離線測試,沙箱不可用時仍達成覆蓋

### 重心說明

**階段一是重心。**本專案無使用者介面,**CLI 的驗證錯誤報告即是唯一的產品介面**。這項做好的展示效果,高於多支援一個國家。

---

## 4. 範圍

### 包含

- 中立發票模型(對齊 EN16931 語意群組)
- EN16931 UBL 2.1 輸出器
- KSeF FA(3) 輸出器
- 驗證鏈:XSD → Schematron
- 文件層級折讓與附加費(BG-20 / BG-21)
- KSeF 2.0 加密(AES-256-CBC + RSA-OAEP)
- KSeF 沙箱客戶端:認證、session、送件、輪詢、UPO 下載
- 本地狀態檔
- CLI 入口與錯誤報告
- 四層測試 + CI

### 不包含(明確排除)

- Peppol 網路連線
- 匈牙利 NAV、法國 PDP、德國等其他國家
- 網頁使用者介面(架構預留,本階段不做)
- **正式環境(production)——僅使用沙箱**
- 多租戶、帳號系統、計費
- 發票儲存與查詢資料庫
- **貸項通知單與更正發票(KOR)**——模型可表達發票類型,但輸出器不處理
- **明細層級折讓與附加費(BG-27 / BG-28)**
- **基數數量(BT-149 / BT-150)**——單價一律以單一計量單位表示
- **UPO 的 XAdES 簽章驗證**——僅儲存,不驗章

---

## 5. 架構

### 資料流

```
                        ①  model                    共用起點
                            │
                ┌───────────┴───────────┐
                ▼                       ▼
            ② ubl                   ④ fa3            轉成 XML
                │                       │
                └───────────┬───────────┘
                            ▼
                      ③ validate                     XSD + Schematron
                            │                        (兩條路都要經過)
                            ▼
                      ⑤ crypto                       加密(僅 FA(3) 路徑)
                            │
                            ▼
                      ⑥ ksef                         送件、輪詢、UPO

                        ⑦  cli                       使用者入口
```

**注意**:`validate` 橫跨兩條路徑——UBL 走 UBL XSD + EN16931 Schematron,FA(3) 走官方 FA(3) XSD。兩者都不讓沙箱當第一個驗證器。

### 模組職責

| 模組 | 負責 | 不負責 |
|---|---|---|
| `model` | 中立發票結構、型別、結構性驗證、稅額彙總推導、捨入 | 不知道 XML,不知道任何國家格式 |
| `ubl` | 中立模型 → EN16931 UBL 2.1 XML | 不做驗證 |
| `fa3` | 中立模型 → KSeF FA(3) XML | 不負責加密或送件 |
| `validate` | XSD 與 Schematron 驗證,回傳結構化錯誤清單 | 不修正錯誤 |
| `crypto` | AES 金鑰產生、RSA-OAEP 包裝、payload 加密 | **不碰網路**——純函式,可離線測試 |
| `ksef` | 認證、開 session、送件、輪詢、下載 UPO、維護狀態檔 | 不判斷發票內容正確性,不自行加密 |
| `cli` | 指令列入口、錯誤報告呈現 | 不含任何商業邏輯 |

### 設計理由

**為何以中立模型為中心**:見第 1 節核心命題。

**為何轉格式與後續處理分屬不同模組**:失敗原因完全不同——前者是輸入資料問題,後者是外部環境問題。拆開後可快速定位,且轉格式模組可完全離線測試。

**為何 `crypto` 獨立於 `ksef`**:加密是**純函式**(輸入明文與金鑰,輸出密文),不涉及網路。獨立後可用已知測試向量離線驗證。若與網路邏輯混在一起,加密錯誤與連線錯誤將難以區分。

**為何 CLI 不含邏輯**:未來加入 FastAPI 時可直接呼叫同一套核心。

---

## 6. 資料模型

### 設計方法

**先對齊 EN16931 的語意群組(BG-\*),再看 FA(3) 需要補什麼。**不先憑直覺設計一個看起來整齊的模型再回頭對應——那樣會產出無法表達合法發票的模型。

每個欄位都應能對應到一個 BT 編號。

### 結構

```python
class Address:
    street: str
    city: str
    postal_code: str
    country: str                    # ISO 3166-1 alpha-2

class Party:
    name: str                       # BT-27 / BT-44
    legal_registration_id: str | None   # BT-30  法人登記號
    vat_id: str | None              # BT-31 / BT-48  VAT 號
    vat_scheme: str                 # 稅號發證國,例如 "PL"
    address: Address

class LineItem:                     # BG-25
    line_id: str                    # BT-126
    name: str                       # BT-153  品名(必填)
    description: str | None         # BT-154  說明(選填)
    quantity: Decimal               # BT-129
    unit_code: str                  # BT-130  UN/ECE Rec 20
    unit_price: Decimal             # BT-146
    net_amount: Decimal             # BT-131
    vat_category: VatCategory       # BT-151
    vat_rate: Decimal               # BT-152
    exemption_reason: str | None        # 供推導 BT-120
    exemption_reason_code: str | None   # 供推導 BT-121

class AllowanceCharge:              # BG-20 折讓 / BG-21 附加費(文件層級)
    is_charge: bool                 # False = 折讓,True = 附加費
    amount: Decimal                 # BT-92 / BT-99
    vat_category: VatCategory       # BT-95 / BT-102
    vat_rate: Decimal               # BT-96 / BT-103
    reason: str | None              # BT-97 / BT-104

class VatBreakdownEntry:            # BG-23 — 完全推導,非輸入欄位
    category: VatCategory           # BT-118
    rate: Decimal                   # BT-119
    taxable_amount: Decimal         # BT-116
    tax_amount: Decimal             # BT-117
    exemption_reason: str | None    # BT-120
    exemption_reason_code: str | None   # BT-121

class Totals:                       # BG-22 — 完全推導
    sum_line_net: Decimal           # BT-106
    allowances_total: Decimal       # BT-107
    charges_total: Decimal          # BT-108
    total_without_vat: Decimal      # BT-109 = 106 - 107 + 108
    total_vat: Decimal              # BT-110
    total_with_vat: Decimal         # BT-112 = 109 + 110
    prepaid_amount: Decimal         # BT-113
    amount_due: Decimal             # BT-115 = 112 - 113

class PolishExtras:                 # 波蘭專屬,具名而非 dict
    exchange_rate: Decimal | None = None
    invoice_kind: PolishInvoiceKind     # RodzajFaktury: VAT / KOR / ZAL ...
    annotations: Annotations            # Adnotacje;確切欄位依 FA(3) XSD 定義

class Invoice:
    number: str                     # BT-1
    issue_date: date                # BT-2
    type_code: InvoiceTypeCode      # BT-3   380 商業發票 / 381 貸項通知單
    currency: str                   # BT-5
    vat_accounting_currency: str | None  # BT-6  多幣別時的申報幣別
    due_date: date | None           # BT-9
    seller: Party
    buyer: Party
    lines: list[LineItem]
    allowance_charges: list[AllowanceCharge] = []
    prepaid_amount: Decimal = Decimal("0.00")   # BT-113
    extras: PolishExtras | None = None

    # 以下為推導欄位,不接受輸入
    vat_breakdown: list[VatBreakdownEntry]
    totals: Totals
```

### 決策一:模型只存語意,不存格式

稅率存 `Decimal("0.23")` 而非 `"23%"`;日期存 `date` 物件而非字串。**格式化是輸出器的責任。**

### 決策二:金額一律 `Decimal`,不使用 `float`

無例外。`float` 無法精確表示十進位小數,累加後產生誤差。

### 決策三:稅別用列舉,不用稅率數值代表

`VatCategory` 為列舉:`S`(標準)、`Z`(零稅率)、`E`(免稅)、`AE`(逆向課稅)、`O`(區域外)等。

**理由**:0% 稅率、免稅、逆向課稅三者稅率都是 `0`,但語意完全不同,輸出也不同。單用 `Decimal` 無法區分。`E` 與 `AE` 另需免稅理由,此約束由模型層強制。

### 決策四:稅額彙總與合計完全推導,不接受輸入

`vat_breakdown` 與 `totals` **皆為推導欄位**,由 `lines`、`allowance_charges`、`prepaid_amount` 計算產生。

**實作機制**:以 Pydantic 的 `@model_validator(mode="after")` 在模型建構完成後計算並填入,並將兩者排除於輸入 schema 之外。使用者無法直接指定,亦無法使模型處於明細與彙總不一致的狀態。

**免稅理由的來源**:免稅理由掛在 `LineItem` 上,而非直接輸入到 `VatBreakdownEntry`。推導時依 `(category, rate)` 分組,組內各行的免稅理由**必須一致**,否則視為資料錯誤。

**約束**:`E`(免稅)與 `AE`(逆向課稅)類別必須提供免稅理由,由模型層強制。

**理由**:若允許人工輸入彙總,明細與彙總可能不一致,而 Schematron 一定會抓到。與其事後被退件,不如讓不一致的狀態根本無法被建構。

### 決策五:波蘭專屬欄位用具名模型,不用無型別 dict

`extras: PolishExtras`,不是 `country_extras: dict`。

**理由**:使用 Pydantic 的目的就是型別安全,留一個無型別 dict 逃生門會產生 `extras["KursWaluty"]` 這類字串鍵程式碼,型別檢查與 IDE 補全全部失效。未來加第二國時再改為 discriminated union。

### 決策六:驗證分層且刻意不重複

- **模型層(Pydantic)**:結構性檢查、推導值的內部一致性。快速、離線、訊息友善。
- **XSD**:語法結構。
- **Schematron(官方)**:跨欄位語意規則。權威來源。

**不以 Pydantic 複製官方 Schematron 規則。**官方規則會改版(現行 v1.3.16 發布於 2026-04-10),自行複製必然不同步。

### 決策七:四捨五入策略

**`Decimal` 只解決精度表示,不解決捨入策略。**EN16931 的 BR-CO-\* 一致性規則抓的正是後者,故須明確定義。

**① 稅額在彙總層計算,不逐行計算**

BR-CO-17 檢查的對象是 BG-23 的彙總(應稅金額 × 稅率 ≈ 稅額),不是逐行檢查。

因此順序為:先依 `(category, rate)` 分組加總得 `taxable_amount`(BT-116),**再**以該總額乘上稅率得 `tax_amount`(BT-117)。

**不可**先逐行算稅額再加總——兩種順序的結果會差幾分錢,且差異會被官方規則抓到。

**② 捨入模式與位數**

所有貨幣金額一律:

```python
amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
```

**理由**:Python `Decimal` 的預設 context 是 `ROUND_HALF_EVEN`(銀行家捨入),但稅務慣例是 `ROUND_HALF_UP`。兩者在 `.005` 給出不同答案,而且是**靜默的差異**。必須明確指定,不依賴預設值。

明細的 `net_amount` 先 quantize 至 0.01 後才進入分組加總,避免中間值的額外小數累積。

**③ 容差**

模型層的一致性檢查採用**與官方規則相同的容差**,不自訂更嚴格的標準。

**理由**:比官方嚴格會拒絕官方實際接受的合法發票,造成假陽性。

確切容差值須對照 EN16931 官方規格確認,不得憑印象設定。

### 刻意不做的抽象

欄位對應直接寫在各自的輸出器內,以清楚的函式命名表達,**不建立設定檔驅動的對應框架**。目前僅兩種格式,額外抽象層不划算。

### 實作時必須查證的項目

下列細節**不得依記憶推測**,必須對照一手資料:

- BT 編號、基數(cardinality)、BR-CO-\* 容差值:對照 EN16931 官方規格
- FA(3) 確切欄位名稱、結構、必填規則:對照官方 FA(3) XSD(schema version 1-0E)
- KSeF 2.0 認證、session 流程、端點路徑:對照 `github.com/CIRFMF/ksef-api` 的 OpenAPI 規格

---

## 7. 欄位落差:四類處理方式

| # | 情況 | 處理 |
|---|---|---|
| 1 | 兩邊皆有 | 直接對應 |
| 2 | 波蘭專屬(EN16931 無) | 置於 `PolishExtras`,不混入主結構 |
| 3 | EN16931 有、FA(3) 無 | 輸出 FA(3) 時捨棄,**回報警告後繼續** |
| 4 | **FA(3) 必填、中立模型無** | **輸出前即失敗,不得送出** |

**第 3 類嚴禁靜默丟棄**——必須明確回報哪些欄位在送往波蘭時會遺失。

**第 4 類的處理方向與第 3 類相反。**FA(3) 是國家申報格式,欄位遠多於 EN16931。這類缺漏送出去必定被退件,因此**在輸出階段就失敗**,避免浪費一次無效送件。

**多幣別是第 1、2 類混合的最佳範例**:發票幣別(BT-5)兩邊都有,但波蘭要求非 PLN 發票仍須以 PLN 申報稅額,FA(3) 因此有匯率欄位(`KursWaluty`),EN16931 則以 BT-6 表達申報幣別。此案例應在 README 中作為落差說明的示範。

---

## 8. 驗證鏈

### 三層,由便宜到昂貴

1. **XSD** — 語法結構。最快、最先抓到錯。UBL 走 UBL 2.1 XSD,FA(3) 走官方 FA(3) XSD。
2. **EN16931 core Schematron** — 跨欄位語意規則。
3. **CIUS 層**(如 Peppol BIS Billing 3.0)— **本專案不做**,僅記錄其存在。

兩條路徑都**先過本地 XSD 再送件**。不讓沙箱當第一個驗證器——那樣慢、浪費配額,且錯誤訊息遠不如本地驗證清楚。

### 驗證資產納入 repo(vendoring)

**決策:將官方預編譯的 `.xslt`、UBL 2.1 XSD、FA(3) XSD 全部納入 repo。**

`.sch` 需先以 ISO Schematron skeleton 編譯為 `.xsl` 才能執行,官方 release 已附預編譯版本。執行時編譯會讓每次測試都付出編譯成本,嚴重拖慢迴圈。

**三份資產必須一起 vendored**,否則「驗證層不需網路」不成立。

**授權與版本紀錄**:EN16931 validation artefacts 有自己的授權條款。納入公開 repo 時須:

- 保留原始授權聲明檔案
- 記錄來源 URL 與版本號(現行 v1.3.16,2026-04-10)
- 於 README 標示這些檔案為第三方資產

### 錯誤報告格式

驗證錯誤須為**結構化物件**而非字串,至少包含:

- 規則 ID(如 `BR-CO-10`)
- 對應的 BT 編號
- 出錯位置(XPath 或明細行號)
- 人類可讀訊息

CLI 據此一次列出所有錯誤。**這是本專案唯一的產品介面,品質即專案門面。**

---

## 9. KSeF 2.0 加密

**KSeF 2.0 強制所有發票加密,互動模式亦不例外。**這是 `ksef` 路徑的主要工作量。

### 規格

| 項目 | 規格 |
|---|---|
| 對稱加密 | AES-256-CBC,PKCS#7 padding |
| 金鑰 | 32 bytes,密碼學安全亂數產生 |
| IV | 16 bytes |
| 金鑰包裝 | RSAES-OAEP(SHA-256 / MGF1) |
| KSeF 公鑰來源 | `GET /api/v2/security/public-key-certificates` |
| 開 session | `POST /api/v2/sessions/online`(互動模式),附 `formCode`(FA(3))與加密後的對稱金鑰 |
| Session 效期 | 12 小時 |

> ⚠️ **上表的端點路徑、參數名稱與 session 效期,須對照 `github.com/CIRFMF/ksef-api` 的 OpenAPI 規格確認後才可實作。**此處僅為設計階段的理解,這類細節最易變動。加密演算法部分(AES-256-CBC + RSA-OAEP/SHA-256)已由兩個獨立來源交叉確認。

### 決策

- **MVP 採互動模式**(`/sessions/online`),不做批次模式。兩者開 session 流程一致,互動模式較易除錯。
- **每個 session 產生新的對稱金鑰**,不重複使用。
- **認證只實作 KSeF token,不做 XAdES 憑證簽章**。token 在測試環境可直接取得,XAdES 複雜度高且非展示重點。

### 為何不使用現成 SDK

PyPI 上已有 `ksef2`(v0.20.0)與 `ksef-python`(v0.1.0),兩者都已封裝加密與 session 管理。**本專案仍選擇自行實作。**

**理由:加密與 session 管理正是本專案要展示的技術內容。**若直接引用 SDK,等於把核心展示點外包,專案退化為「呼叫別人的函式」。

這些套件的價值在於**作為對照組**:實作卡關時可對照其原始碼,理解官方文件未言明的細節。

---

## 10. 本地狀態與 UPO 處理

送件流程橫跨多次網路往返且可能中斷,故需要持久化的本地狀態。

### 狀態檔

- 每張送出的發票對應一筆紀錄,以**發票編號**為鍵
- 紀錄至少包含:發票編號、KSeF 參考編號、session 識別、送出時間、目前狀態、UPO 檔案路徑
- **參考編號在送出前寫入**,不是收到回應後才寫。未收到回應不等於未送達。
- 狀態檔位置納入 `.gitignore`(可能含 KSeF 編號等識別資訊)

### UPO 的處理

UPO 是官方收據,為送件成功的唯一憑證。

- **儲存**:以發票編號為檔名存為獨立 XML 檔,不塞進狀態檔
- **回寫**:KSeF 編號與 UPO 路徑寫回該筆狀態紀錄
- **不驗證 XAdES 簽章**:UPO 帶有官方 XAdES 簽章,本專案僅儲存、不驗章。驗章需要完整的憑證鏈驗證,複雜度高且非展示重點,已列入「已知限制」。

### 中斷復原

程式中斷後重啟,應能讀取狀態檔,對所有「已送出但未取得 UPO」的紀錄**憑參考編號續查**,而非重送。

---

## 11. 錯誤處理

### 三類錯誤

| 類型 | 例子 | 處理 |
|---|---|---|
| 資料錯誤 | 缺必填欄位、稅額不一致 | **不重試**,一次回報全部 |
| 暫時性外部錯誤 | 網路逾時、沙箱 503 | **重試**,指數退避,有次數上限 |
| 永久性外部錯誤 | 認證失敗、憑證過期、官方拒絕 | **不重試** |

**資料錯誤必須一次回報全部**,不可遇到第一個即中斷。

### 重試的唯一原則

**只對冪等操作重試。**查詢狀態、下載 UPO 可重試;送件不可。這與「禁止自動重送」是同一條原則的兩種表述。

### KSeF 非同步流程

送出 → 取得參考編號 → 輪詢狀態 → 成功後下載 UPO。因此:

- **輪詢須有超時上限**,不得無限等待
- **中間狀態須持久化**(見第 10 節)
- **嚴禁自動重送**:未收到回應不等於未送達。正確行為是**查詢**而非重送

### 安全

- token、金鑰、憑證**不得寫入程式碼,不得 commit**
- 使用環境變數搭配 `.env`,`.env` **須**列入 `.gitignore`
- 本專案將公開於 GitHub,金鑰一旦進入 git 歷史即無法真正移除

---

## 12. 測試策略

| 層級 | 測試內容 | 需網路 | 預設執行 |
|---|---|---|---|
| 單元 | 模型驗證、推導計算、**捨入邊界**、兩個輸出器、**crypto 測試向量** | 否 | ✅ |
| 驗證 | 產出通過官方 XSD 與 Schematron | 否 | ✅ |
| **ksef 離線** | **以 `respx` mock httpx,測狀態機** | 否 | ✅ |
| 整合 | 真實送入 KSeF 沙箱 | 是 | ❌ pytest marker |

### 為何需要「ksef 離線」層

若 `ksef` 只在整合測試中執行,而整合測試預設關閉,則**最複雜、最易出錯的模組在日常測試中覆蓋率為零**。

解法:將沙箱回應錄製為 fixture,以 `respx` 攔截 httpx,離線測試狀態機——輪詢、逾時、退避、UPO 解析、中斷復原、各錯誤碼分支。如此「沙箱不可用時前三層仍可完整通過」才真正成立。

### 捨入測試

決策七的三項規則各需對應測試,特別是:

- `.005` 邊界值(驗證確實使用 `ROUND_HALF_UP` 而非預設的 `ROUND_HALF_EVEN`)
- 「先分組加總再算稅」與「先逐行算稅再加總」結果不同的案例
- 容差邊界

### 其他

- **黃金檔案測試**:保存已知正確的 UBL 與 FA(3) XML 作為基準
- **測試素材**:使用官方範例 XML,不自行編造
- **CI**:GitHub Actions 執行前三層,README 顯示狀態 badge

---

## 13. 技術選型

| 項目 | 選擇 | 理由 |
|---|---|---|
| 語言 | Python | 既有基礎所在;資料處理與 XML 生態完整 |
| 資料模型 | Pydantic | 型別驗證與錯誤訊息品質佳,`model_validator` 支援推導欄位 |
| XML | lxml | 成熟,XSD 驗證支援完整 |
| Schematron | **saxonche** | **關鍵**:EN16931 Schematron 需 XSLT 2.0,lxml 僅支援 1.0 |
| 加密 | cryptography | AES-CBC 與 RSA-OAEP 的標準選擇 |
| HTTP | httpx | 逾時與重試控制完整,且有 respx 可 mock |
| HTTP mock | respx | 讓 `ksef` 模組可離線測試 |
| 測試 | pytest | marker 機制符合分層測試需求 |

### 為何不改用 .NET/Java

該生態的 EN16931 函式庫較成熟且原生支援 XSLT 2.0。但 `saxonche` 可透過 pip 安裝並解決 XSLT 2.0 問題,不足以構成放棄既有 Python 基礎的理由。

---

## 14. 已知限制

- 僅支援波蘭一國,不宣稱為多國方案
- 僅連接沙箱,未經正式環境驗證
- 欄位對應涵蓋常見情境,非 FA(3) 完整規格
- **未涵蓋貸項通知單與更正發票(KOR)**——此在波蘭為重要場景
- **認證僅實作 KSeF token,未實作 XAdES 憑證簽章**
- **UPO 僅儲存,不驗證其 XAdES 簽章**
- 僅互動模式,未實作批次模式
- 不支援明細層級折讓與附加費(BG-27 / BG-28)
- 不支援基數數量(BT-149 / BT-150)
- 無使用者介面
- 未實作 CIUS 層驗證(如 Peppol BIS Billing 3.0)

---

## 15. 未來可能延伸(本階段不做)

- 在核心之上加一層 FastAPI,提供 HTTP API 與 Swagger UI
- 新增第二國(此時才有足夠資訊決定如何抽象對應層)
- 支援 KOR 更正發票
- UPO 簽章驗證
- 產出人類可讀的發票 PDF
