# 🤖 Modular Quant Trading System (Exness MT5 & Gold XAU/USD)

Hệ thống giao dịch định lượng mô-đun (Modular Quant Trading System) tự động cho thị trường Vàng (`XAU/USD`), kết nối trực tiếp với sàn **Exness** qua **MetaTrader 5 Terminal** và tích hợp trí tuệ nhân tạo **Gemini 3.8 Flash** để phân tích tin tức kinh tế vĩ mô.

---

## 🏛️ Kiến trúc 5 Tầng (Modular Architecture)

1. **Tầng Dữ liệu (`app/data/`)**: MT5 Read-only adapter, Data Validator (nhận diện Gold daily session break & weekend gap), SQLite persistence với Upsert chống trùng.
2. **Tầng Trí tuệ (`app/intelligence/`)**: Market Analyzer (EMA đa khung 21/50/200, ATR(14), Dynamic Spread Ratio), Economic Calendar (ForexFactory feed UTC), News Extractor (Gemini 3.8 Flash trích xuất cơ chế vĩ mô, tính Surprise score thuần Python), Feature Store có đo lường độ trễ (latency tracking).
3. **Tầng Chiến lược & Kiểm chứng (`app/strategy/` & `backtests/`)**: Baseline Benchmark (EMA Crossover không dùng AI), Prediction Model (Platt Scaling Calibrated Classifier với kiểm định Brier Score $\le 0.25$), Signal Engine tính Expected Value ($EV > 0$), Backtest Engine mô phỏng spread động, slippage và phí, Walk-Forward Validator (70% In-sample / 30% Out-of-sample).
4. **Tầng Quản trị Rủi ro & Thực thi (`app/risk/` & `app/execution/`)**: Risk Manager Veto Engine (Lot size tự động $0.5\%$ vốn/lệnh, Daily Loss Limit $2\%$, bộ lọc spread kép, Session Guard, News Shield), MT5 Execution với Magic Number độc nhất + Hard SL/TP, Reconciliation Engine (Broker as SSOT & Crash recovery sau sự cố).
5. **Tầng Giám sát & Vận hành (`app/monitoring/`)**: Circuit Breaker (Safe mode khi chuỗi 5 lệnh thua hoặc mất mạng 60s), Emergency Kill Switch (thanh lý toàn bộ lệnh trong $\le 5$ giây), Telegram Bot Alert (báo cáo P&L, lệnh mở/đóng, cảnh báo veto), Performance & Drift Tracker (theo dõi trượt giá slippage thực tế).

---

## 🚀 Hướng dẫn Chạy & Kiểm tra Cổng Nghiệm thu (Quality Gates)

Môi trường ảo đã được thiết lập sẵn trong `.venv`.

### 1. Chạy nghiệm thu từng Phase:
```powershell
# Nghiệm thu Phase 1: Nền tảng Dữ liệu & MT5 Read-only
.\.venv\Scripts\python.exe -m app.main --phase 1

# Nghiệm thu Phase 2: Đặc trưng Kỹ thuật, Lịch kinh tế & AI Gemini
.\.venv\Scripts\python.exe -m app.main --phase 2

# Nghiệm thu Phase 3: Mô hình Calibrated ML, Dự đoán EV & Walk-forward Backtest
.\.venv\Scripts\python.exe -m app.main --phase 3

# Nghiệm thu Phase 4: Quản trị Rủi ro, Veto Engine, Chống trùng lệnh & Phục hồi Crash
.\.venv\Scripts\python.exe -m app.main --phase 4

# Nghiệm thu Phase 5: Vòng lặp Vận hành, Circuit Breaker, Kill Switch & Performance
.\.venv\Scripts\python.exe -m app.main --phase 5
```

### 2. Chạy toàn bộ 26 bài kiểm thử tự động (Unit & Fault Injection Tests):
```powershell
.\.venv\Scripts\pytest.exe -v tests/
```

### 3. Kích hoạt Cắt mạch Khẩn cấp (Emergency Kill Switch):
Để đóng toàn bộ vị thế và hủy toàn bộ lệnh ngay lập tức mà không cần mở terminal:
Tạo một file rỗng có tên `kill_switch.flag` tại thư mục gốc:
```powershell
New-Item -Path "kill_switch.flag" -ItemType File
```
Bot sẽ phát hiện file cờ, đóng mọi vị thế trong vòng dưới 1 giây và chuyển sang trạng thái dừng an toàn.

---

## 📁 Cấu trúc Thư mục

```text
my-bot/
├── app/
│   ├── config.py                     # Quản lý cấu hình & biến môi trường
│   ├── bot_loop.py                   # Vòng lặp điều phối chính (Autonomous Orchestrator)
│   ├── main.py                       # CLI Runner kiểm tra 5 Quality Gates
│   ├── core/                         # Constants, Enums, Pydantic v2 Contracts
│   ├── data/                         # MT5 Feeds, Data Validator, SQLite Storage, Economic Calendar
│   ├── intelligence/                 # Market Analyzer, News Extractor (Gemini), Feature Store
│   ├── strategy/                     # Baseline Strategy, Prediction Model, Signal Engine (EV)
│   ├── risk/                         # Risk Manager (Veto Engine, Lot sizing, Daily loss)
│   ├── execution/                    # MT5 Adapter (Idempotency, Hard SL/TP), Reconciliation (SSOT)
│   └── monitoring/                   # Logger, Circuit Breaker, Telegram Bot, Performance Tracker
├── backtests/
│   ├── backtest_engine.py            # Backtest với spread động, slippage và phí
│   └── walk_forward.py               # Phân chia thời gian 70/30 In-sample & Out-of-sample
├── tests/                            # 26 bài test tự động cho cả 5 Phase
├── .env                              # Thông tin tài khoản & API keys (đã bảo mật)
├── .env.example
├── pytest.ini
├── requirements.txt
└── ROADMAP.md                        # Lộ trình 5 Phase & Tiêu chí nghiệm thu chuẩn
```
