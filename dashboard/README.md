# Batch Dashboard (in-ovo)

Dashboard web cục bộ để theo dõi một session thí nghiệm: từng quả trứng, phổ theo thời gian và ảnh theo từng batch.

## Flow

```
Mở app ──► Màn hình bắt đầu (trống, chưa đọc dữ liệu nào)
             │
             ├─ ① Tải dữ liệu lên ── chọn thư mục session / batch_XX_… / thư mục ảnh
             │       └─ server kiểm tra file đã có → chỉ gửi file mới → lưu vào --store
             │
             └─ ② Kết nối MongoDB ── URI + database → "Kiểm tra kết nối" → chọn session
                                      │
                         ┌────────────┘
                         ▼
                 Phân tích (trừ dark, band means, ΔA, thumbnail ảnh)
                         ▼
                 Dashboard của nguồn đó  ──  "+ Dữ liệu" để thêm nguồn khác,
                                             "Đóng nguồn" để bỏ, dropdown "Nguồn" để chuyển
```

- Server **không** tự quét thư mục lưu trữ khi khởi động. Chỉ những nguồn bạn đã upload / kết nối trong phiên server này mới được phân tích.
- Nguồn upload chỉ thay đổi khi bạn tải thêm (tải batch mới vào cùng session → tự phân tích lại).
- Nguồn MongoDB có tuỳ chọn **Đồng bộ Mongo**: hỏi lại database mỗi 15 s, batch/phép đo mới tự hiện (nhãn MỚI).
- "Nguồn đã dùng gần đây" chỉ là lối tắt — chỉ mở khi bạn bấm. URI Mongo được lưu **đã che mật khẩu**.

## Chạy

```
python dashboard/server.py                          # http://127.0.0.1:8050
python dashboard/server.py --store data/BATCH_B     # nơi lưu file upload (mặc định data/BATCH_B)
python dashboard/server.py --host 0.0.0.0           # cho máy khác trong LAN dùng
```

Hoặc nhấp đúp `dashboard/run_lan.bat` để chạy ở chế độ LAN; cửa sổ sẽ in ra các địa chỉ. Máy khác dùng địa chỉ `192.168.1.x` (các IP `172.30…`, `192.168.18…`, `192.168.199…` là card ảo của Hyper-V/VMware).
Dashboard chưa có đăng nhập: chỉ dùng trong mạng LAN tin cậy.

### Chạy nền liên tục trong LAN (không cần mở script mỗi lần)

```
powershell -ExecutionPolicy Bypass -File dashboard\install_autostart.ps1              # cài (1 lần)
powershell -ExecutionPolicy Bypass -File dashboard\install_autostart.ps1 -Uninstall   # gỡ
```

- Tạo task **InovoDashboard** trong Task Scheduler: chạy `dashboard\service.pyw` (không cửa sổ) mỗi khi đăng nhập Windows.
- `service.pyw` chạy `server.py --host 0.0.0.0 --port 8050`, **tự khởi động lại** khi server lỗi/dừng và khi file `.py` trong `dashboard/` thay đổi. Sửa file giao diện (`static/`) chỉ cần tải lại trang.
- Mở cổng TCP 8050 trên firewall cho mạng Private (Windows hỏi UAC một lần).
- Log: `outputs/dashboard_logs/server.log`. Khi server khởi động lại, các nguồn đang mở mất → mở lại từ mục **Gần đây**.
- Nếu cổng 8050 đang bị chiếm (vd đang chạy `run_lan.bat`), service chờ đến khi cổng trống.

Cần numpy, Pillow, plotly (lấy plotly.min.js → chạy offline); `pymongo` nếu dùng MongoDB.

## Schema MongoDB (mặc định — đổi tên collection trong mục "nâng cao")

| collection | document |
|---|---|
| `sessions` (tuỳ chọn) | nội dung `session_info.json` + `session` (lấy `incubation_start`) |
| `measurements` | 1 document = 1 dòng `metadata_log.csv` (`session`, `batch_no`, `measurement_type` = dark/reference/embryo, `sample_id`, `timestamp`, `egg_label`, `egg_end`, `egg_id`, `incubation_age_h`, `peak_adc`, …) + `wavelengths: [nm]` + `counts: [Raw_ADC_Counts]` (+ `batch_folder` tuỳ chọn) |
| `images` | `{session, batch_no, egg_label, timestamp, filename}` + một trong `gridfs_id` (bucket `images`) / `data` (Binary) / `path` |

Đẩy một session thư mục lên Mongo:

```
python dashboard/mongo_import.py --uri mongodb://localhost:27017 --db inovo \
    --session-dir data/BATCH_B/20261007_105144_embryo_session --images gridfs
```

Chạy lại an toàn (upsert phép đo theo `session + sample_id`, ảnh đã có thì bỏ qua). Ảnh BMP ~24 MB nên phải dùng GridFS (giới hạn document 16 MB) hoặc `--images path`.

## Giao diện

- **Thanh tiêu đề**: 5 tab (Từng trứng · Tình trạng · Hình dạng trứng · So sánh · **Phân tích**). Tab Phân tích chọn theo câu hỏi: *Trứng nào không phôi?* (Học máy) · *Trứng nở tách được Trống / Mái?* (`sexclust.py`, `static/sex.js`, `/api/sex`) · *Phân cụm tự do*. Mô phỏng bài báo nằm cuối tab So sánh, chọn / đóng nguồn, Đồng bộ (chỉ nguồn Mongo), phân tích lại (R), nút ⚙ (**Làm mượt**, **Band**, **Trục λ** — dùng chung cho mọi tab), "+ Dữ liệu", **ngôn ngữ**, hướng dẫn, sáng/tối. Mỗi tab chỉ để điều khiển chính trên thanh; tuỳ chọn ít dùng nằm trong nút ⚙ của tab.
- Hướng dẫn và giải thích thuật toán nằm trong hộp thoại mở bằng nút **?**, không hiển thị sẵn trên trang.
- **Ngôn ngữ**: Tiếng Việt (gốc), English, 繁體中文, 简体中文 — lưu trong trình duyệt, đổi thì trang tải lại. `static/i18n.js` (bộ máy) + `static/i18n_data.js` (từ điển): khoá = câu tiếng Việt trong code, `t("câu", {tham số})` ở JS; text tĩnh trong HTML được dịch tự động nếu khớp khoá; khối hướng dẫn dài dùng `data-i18n`; thông điệp lỗi từ server dịch ở client qua `tMsg` (khớp nguyên câu hoặc mẫu regex `I18N_PAT`). Thêm chuỗi mới = thêm một dòng `"câu tiếng Việt": [English, 简体, 繁體]` vào `i18n_data.js`.

## Tính năng

### Tab Từng trứng
- **Khung Batch** (thu gọn, bấm để mở): thời gian đo, tuổi ấp, số phổ/ảnh, lamp peak, ghi chú `note.rtf`, cảnh báo; đồ thị lamp theo tuổi ấp. Khi thu gọn vẫn hiện số batch mới, số cảnh báo, số batch chưa có ảnh.
- **Danh sách trứng** (thumbnail ảnh mới nhất, chấm màu = nhóm đã gán). ↑/↓ để đổi trứng.
- **Phổ theo từng batch** (Đầu đo tù / nhọn / cả hai · Đại lượng · Làm mượt). Click lên phổ để chọn λ (±5 nm) theo dõi ở **Time series**.
- Khung tab con: **Ảnh** (timestamp ảnh/phổ, lightbox zoom, so sánh 2 batch, ← → đổi batch) · **Bản đồ phổ** (λ × batch) · **Phép đo** (exposure, peak ADC, bão hoà, color ratio, frame RSD, số lần đo…) · **Tổng quan trứng × batch** (click ô để mở trứng).
- **Raw ADC (không xử lý)**: đúng cột `Raw_ADC_Counts` trong file, không trừ dark / làm mượt / nội suy / che vùng / cắt trục; Dark và Lamp gốc từng batch bật ở chú thích.

### Tab Hình dạng trứng (`shape.py`, `static/shape.js`, `/api/shape`)
- Đọc `<session>/outputs_shape/*.csv` (chiều dài L / rộng B từ `src/egg_size.py`, mỗi file = 1 lần đo cả khay). Profile mỗi trứng = trung vị L, B các lần đo `ok`, bỏ lần lệch > max(3·MAD, 1 mm).
- Đường viền theo mô hình **Hügelschäffer** `y = ± B/2·√((L² − 4x²)/(L² + 8wx + 4w²))`, w = (w/L)·L; w/L khớp từ đường viền thật của `outputs_shape/egg_size/captures/*.png` (cache ở `outputs/dashboard_cache/shape_fit_*.json`, mặc định 0.046), sửa tay được.
- Tích phân trên đường viền (client): chu vi ∮ds, diện tích ½∮(x dy − y dx), thể tích π∫y²dx, diện tích vỏ 2π∫y ds. Hiện đường viền + từng lần đo, chồng tất cả trứng, L/B theo thời gian, scatter hình dạng, bảng (TB ± SD nhóm, |d|).
- **Từ pixel ảnh: vi phân → tích phân** (`/api/shape_px`, `/api/shape_pximg`, `shape.pixel_egg`): với từng ảnh batch của trứng, mặt nạ GrabCut cho tâm + mép gần đúng; theo 720 tia lấy I(r) của ảnh R − B, mép = chỗ dI/dr âm nhất (nội suy parabol, dưới pixel); bỏ điểm lệch, làm trơn r(θ) bằng chuỗi Fourier bậc 12; đổi sang mm theo `outputs_shape/egg_size/calibration.json`, đầu tù sang trái; tích phân P, A, V, S với y(x) = nửa bề rộng đo được; khớp Hügelschäffer cho w/L riêng của trứng. Lần đầu ~15 s/trứng (4 luồng), cache ở `outputs/dashboard_cache/<nguồn>/shape_px/`. Cần OpenCV + scipy và `src/egg_size.py`.
- L, B, SI, thể tích được thêm vào bảng đặc trưng (tab So sánh, Phân cụm) và chip ở tab Từng trứng. Upload session cho phép thư mục `outputs_shape`.

### Tab So sánh
Chọn N trứng, gán nhóm **Trống / Mái / Không phôi** (từng trứng, "Gán cho đã chọn", hoặc nhập danh sách; lưu ở `outputs/dashboard_tags/<session>.json`, dùng chung cho nguồn upload và Mongo). Gồm: phổ (từng trứng hoặc TB ± SD nhóm), đường phát triển theo tuổi ấp, Cohen's d giữa các nhóm theo bước sóng, scatter + bảng đặc trưng (độ sáng vỏ, ΔA cuối, ΔA/ngày, R/NIR, ΔR/NIR, λ đỉnh, peak ADC, PC1/PC2 hình dạng phổ, ΔS/R ở 2 vùng của bài báo) kèm TB ± SD nhóm và |d| lớn nhất.

### Mô phỏng bài báo (cuối tab So sánh)
Chọn **Tất cả trứng** hoặc **Chỉ trứng đã nở** (theo tab Tình trạng); đầu đo dùng chung với tab So sánh.
Tái hiện Li et al. 2026 (*Animals* 16:2796, DSSF-Net) trên toàn bộ trứng: cơ sở T hoặc counts; Batch A ≈ D2, Batch B ≈ D5; Hình 7 (phổ TB theo batch), Hình 8 (chênh lệch nhóm theo batch), ΔS và R theo nhóm; vùng 594–620 / 660–675 nm tô vàng; sàng lọc SVM (RBF, C = 10) + AUC kiểm định chéo cho từng batch và mọi cặp batch × 4 bộ đặc trưng (chạy nền, `paper.py`), kèm **kiểm định hoán vị toàn bộ quá trình**. Với N nhỏ, max-AUC ngẫu nhiên thường 0.8–0.9 — chỉ tin AUC vượt rõ ngưỡng này.

### Tab Phân cụm — không giám sát (`unsup.py`, `static/unsup.js`)
- **Dữ liệu đầu vào**: phổ 1 batch · **phổ 1 ngày** · **ghép 2 ngày** (S_A ‖ S_B) · **ghép 2 ngày + Δ** (S_A ‖ S_B ‖ S_A − S_gốc ‖ S_B − S_gốc; ví dụ D3, D4, D3 − D2, D4 − D2) · **như bài báo** (S_gốc ‖ S_sau ‖ ΔS ‖ R, mặc định T, D2 → D5, ε = 0.02; bỏ bước sóng có S_gốc < 5% đỉnh) · ΔS giữa 2 batch · quỹ đạo theo tuổi ấp · bảng đặc trưng.
- **Đại lượng phổ**: hình dạng, **min–max 0–1 từng phổ**, **A (OD)**, **A bỏ offset** (A − trung bình của chính phổ trong cửa sổ λ: bỏ dịch đều do vỏ / cách đặt / đèn), ΔA, T, counts, raw. **Chuẩn hoá biến** (trong ⚙ của tab): z-score, **min–max 0–1**, không.
- **Bố cục**: bản đồ 2D + hồ sơ cụm luôn hiện; các phần còn lại là sub-tab Kết quả · Số cụm k · PCA · Dendrogram · Bất thường · Ổn định · Quét ngày (nút ? giải thích sub-tab đang mở).
- **Kết quả**: bản đồ 2D (PCA / t-SNE / MDS; màu = cụm, hình = nhóm), hồ sơ TB ± SD mỗi cụm (các khối ghép vẽ nối tiếp, có tên khối), khung chẩn đoán (Chọn k: silhouette, elbow, BIC · PCA: phương sai + loadings · Dendrogram · Bất thường: Isolation Forest + LOF), bảng từng trứng, ARI/NMI so với nhóm đã gán.
- **Cụm tham chiếu & độ ổn định**: "Đặt cụm hiện tại làm tham chiếu" ghi lại cụm của từng trứng (lưu trong trình duyệt theo session). Sau đó hình của mỗi trứng = cụm tham chiếu (tròn, vuông, tam giác, …) và không đổi khi đổi dữ liệu / batch / ngày / thuật toán; màu = cụm hiện tại, được khớp với cụm tham chiếu (cùng màu = cùng cụm). Khung "Ổn định cụm" cho ARI với tham chiếu, % trứng giữ cụm, bảng chéo, danh sách trứng đổi cụm. **Theo dõi theo batch & ngày** phân cụm lại từng batch và từng ngày, vẽ ARI theo tuổi ấp và bảng trứng × thời gian (số lần đổi cụm của mỗi trứng). Quét theo ngày có thêm thước đo "ARI với tham chiếu".
- **Quét theo ngày** (`/api/unsup_scan`): với các ngày được bật (mặc định D2–D6) chạy cùng thuật toán cho từng ngày, mọi cặp 2 ngày và mọi bộ gốc < A < B (ghép 2 ngày + Δ). Hiện PC1–PC2 của từng ngày, ma trận ngày × ngày, xếp hạng các bộ ghép + Δ theo silhouette / ARI / NMI; click để mở cấu hình đó ở phần chi tiết.

### Tab Học máy (`ml.py`, `static/ml.js`, `/api/ml`)
- **Mục tiêu**: *Tìm trứng không phôi* (hiện tại) hoặc *Trống / Mái* (khi đã có nhãn giới tính).
- **Đặc trưng**: *thay đổi phát triển* (so với ngày đầu, mỗi ngày sau × mỗi đầu đo: đổi hình dạng phổ, ΔA band, đổi R/NIR) · *phổ ngày A, B + Δ so với ngày gốc* · cả hai. Đầu đo: cả hai / tù / nhọn.
- **Khi ít nhãn** (xếp hạng ứng viên không phôi): *chỉ số phát triển* (không dùng nhãn), *giống trứng đã biết* (khoảng cách tới trứng không phôi đã gán), *PU learning* (logistic, dương = không phôi đã gán, âm = mẫu ngẫu nhiên trứng chưa gán); *điểm tổng hợp* = TB phân vị hạng.
- **Kiểm chứng**: trứng không phôi đã biết đứng thứ mấy theo chỉ số phát triển (độc lập với nhãn). Với session hiện tại: **0000 đứng 1/31**; ứng viên tiếp theo 0012, 0002, 0020, 0004, 0001 (ghi chú soi trứng B09 nêu 12, 20, 24 "không thấy mạch máu").
- **Vòng lặp gán nhãn**: gán *Không phôi* / *Có phôi* ngay trong bảng → tự chạy lại. Đủ ≥ 3 mỗi lớp → **học có giám sát** (Logistic / SVM / Random Forest, chuẩn hoá + PCA trong từng fold, AUC kiểm định chéo phân tầng lặp lại, kiểm định hoán vị, dự đoán trứng chưa gán).
- **Quỹ đạo phát triển**: đổi hình dạng / ΔA theo ngày của mọi trứng; đỏ = không phôi đã gán, cam = 5 ứng viên đầu, xanh lá = có phôi đã gán.
- Nhóm mới **Có phôi** (`fertile`) = có phôi nhưng chưa rõ giới; Trống / Mái cũng tính là có phôi.

## Thuật ngữ

### Thời gian

| Thuật ngữ | Ý nghĩa |
|---|---|
| **Session** | Một đợt thí nghiệm (thư mục `…_embryo_session`), có `incubation_start` trong `session_info.json`. |
| **Batch** | Một lượt đo toàn bộ trứng (`batch_XX_…`): dark, reference (lamp), phổ từng trứng ở 2 đầu, ảnh. |
| **Tuổi ấp (h)** | Thời gian từ `incubation_start` đến lúc đo. |
| **Ngày (D*n*)** | Ngày lịch của lần đo trừ ngày lịch bắt đầu ấp: bắt đầu 06/10 → đo 08/10 là D2. Các batch cùng ngày được gộp; **phổ 1 ngày** = trung bình các batch đó của mỗi trứng (như bài báo trung bình nhiều lần đo/ngày). |
| **Ngày gốc** | Ngày tham chiếu để tính hiệu Δ trong "ghép 2 ngày + Δ" (khi quét: gốc < A < B). |

Session hiện tại `20261007_105144_embryo_session` (bắt đầu ấp 06/10 17:00, 9 batch, 31 trứng):

| Ngày | Batch | Tuổi ấp |
|---|---|---|
| D1 | B01 | 18 h |
| D2 | B02 + B03 | 42 h, 48 h |
| D3 | B04 + B05 | 66 h, 72 h |
| D4 | B06 + B07 | 91 h, 97 h |
| D5 | B08 | 113 h |
| D6 | B09 | 144 h |

D1 chỉ có 1 batch rất sớm nên mặc định không đưa vào quét. Ở B09: trứng 0020 thiếu ảnh; 0002, 0008, 0028 có phổ bão hoà; ghi chú B09 báo 13 trứng phổ nhiễu.

### Đo và đại lượng phổ

| Thuật ngữ | Ý nghĩa |
|---|---|
| **Đầu tù / đầu nhọn** | Hai vị trí đo trên quả trứng (`egg_end` = blunt / pointed); phân tích riêng từng đầu. |
| **Raw ADC** | Giá trị `Raw_ADC_Counts` trong file, không xử lý. |
| **Dark (D)** | Phổ khi tắt đèn của cùng batch — nền của cảm biến. |
| **Lamp / reference (L)** | Phổ đèn không có trứng. Đèn **trôi mạnh giữa các batch** (lamp peak ~17k → ~53k ở session hiện tại), nên đại lượng phụ thuộc lamp khó so giữa các ngày. |
| **Counts (S)** | Raw ADC trừ dark của cùng batch. |
| **T** | Truyền qua tương đối `(S − D) / (L − D)`, như bài báo. Phụ thuộc lamp. |
| **A** | Độ hấp thụ `−log10 T`. |
| **ΔA** | `log10(S₀ / S)`: thay đổi so với lần đo đầu của **chính quả trứng**; chỉ dùng counts nên không phụ thuộc lamp. > 0 = hấp thụ tăng. Bỏ vùng tín hiệu < 4% cực đại. |
| **Hình dạng (norm)** | Counts / trung bình counts trong band — bỏ khác biệt độ sáng vỏ, giữ hình dạng phổ. |
| **Min–max 0–1 (từng phổ)** | Mỗi phổ (counts đã làm mượt) đưa về 0–1 trong cửa sổ Trục λ; áp dụng trước khi lấy trung bình ngày và trước khi tính Δ. |
| **Band** | Dải bước sóng lấy trung bình cho time series, tổng quan, đặc trưng (mặc định 604–922 nm). |
| **Trục λ** | Khoảng bước sóng hiển thị và dùng làm biến cho phân tích phổ (mặc định 580–960 nm). |
| **Bão hoà** | Phổ chạm trần ADC — giá trị vùng đó không tin được. |
| **ΔS, R** | `ΔS = S_B − S_A` (thay đổi giữa 2 thời điểm), `R = ΔS / (S_A + ε)` (thay đổi tương đối); ε = 0.02 với T, 0.02 × counts trung vị với counts. |
| **Vùng bài báo** | 594–620 nm và 660–675 nm — vùng quan trọng nhất cho phân biệt giới tính theo Li et al. (tô vàng trên đồ thị). |

### Thống kê và học máy

| Thuật ngữ | Ý nghĩa |
|---|---|
| **Cohen's d** | Chênh lệch trung bình 2 nhóm / độ lệch chuẩn gộp; \|d\| ≥ 0.8 là khác biệt lớn. |
| **z-score** | Chuẩn hoá từng biến về trung bình 0, độ lệch chuẩn 1. |
| **Min–max 0–1 (theo biến)** | Chuẩn hoá từng biến về 0–1 trên tất cả trứng (nhạy với ngoại lai). |
| **PCA, PC1/PC2, loadings** | Các hướng biến thiên lớn nhất; loadings = trọng số từng bước sóng. Phân cụm chạy trên các PC giữ ~95% phương sai. |
| **t-SNE, MDS** | Bản đồ 2D phi tuyến; t-SNE giữ láng giềng (khoảng cách giữa cụm không có nghĩa), MDS giữ khoảng cách. |
| **K-Means, GMM, Ward, DBSCAN** | Các thuật toán phân cụm; DBSCAN không cần k và gán điểm thưa là "Nhiễu". |
| **Silhouette** | −1 → 1: cụm tách rõ đến đâu. > 0.5 rõ, 0.25–0.5 yếu, < 0.25 gần như không có cụm. So giữa các cấu hình chỉ mang tính tương đối. |
| **Elbow, BIC** | Chọn k: chỗ inertia "gãy khuỷu", BIC thấp nhất. |
| **Isolation Forest, LOF** | Phát hiện trứng bất thường; đánh dấu đỏ khi Isolation top 10% và LOF > 1.5. |
| **ARI, NMI** | Mức trùng giữa cụm và nhóm đã gán (1 = trùng hoàn toàn, ≈ 0 = ngẫu nhiên). Cần ≥ 4 trứng đã gán, ≥ 2 nhóm. |
| **Chỉ số phát triển** | TB z-score các đặc trưng thay đổi so với ngày đầu (dấu: cao = phát triển nhiều). Âm nhiều = ít phát triển → ứng viên không phôi. Không dùng nhãn. |
| **PU learning** | Positive–Unlabeled: học từ vài mẫu dương đã biết và nhiều mẫu chưa gán (coi tạm là âm, lấy mẫu ngẫu nhiên nhiều lần). |
| **Kiểm định chéo (CV), điểm out-of-fold** | Chia trứng có nhãn thành k phần, học trên k − 1 phần và chấm phần còn lại; mỗi trứng được chấm khi nó không nằm trong tập học. |
| **AUC, kiểm định hoán vị** | Mô phỏng bài báo (tab So sánh): AUC phân loại SVM kiểm định chéo; xáo nhãn và chạy lại toàn bộ sàng lọc để biết AUC "may mắn" cao đến đâu. |

## Quy ước dữ liệu

- Danh tính trứng = `egg_label` trong metadata (không đọc từ tên file phổ).
- Ảnh thư mục: `<N>__YYYY-MM-DD__HH-MM-SS.bmp`, N = egg_label (có hoặc không pad 0), trong bất kỳ thư mục con nào của batch.
- Counts trừ dark của chính batch đó; ΔA bỏ vùng tín hiệu < 4% cực đại (2 đầu dải phổ).
- Thumbnail JPEG cache ở `outputs/dashboard_cache/<id nguồn>/` (xoá thoải mái).

## Mã nguồn

- `server.py` — HTTP, đăng ký nguồn đã mở, upload, thumbnail
- `stores.py` — `FolderStore` / `MongoStore` (cùng một mô hình batch)
- `analysis.py` — phân tích dùng chung
- `paper.py` — sàng lọc SVM + hoán vị (job nền) cho phần mô phỏng bài báo ở tab So sánh
- `unsup.py` — thuật toán không giám sát
- `ml.py` — tab Học máy (đặc trưng phát triển, xếp hạng ứng viên, học có giám sát)
- `mongo_import.py` — đẩy session thư mục lên Mongo
- `static/index.html` — giao diện; `static/compare.js`, `static/paper.js`, `static/unsup.js` — tab So sánh (kèm mô phỏng bài báo), Không giám sát
