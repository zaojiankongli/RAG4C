// RAG4C Console —— Tauri 入口。
//
// 说明：本应用是「纯前端壳 + HTTP 桥」模式——RAG 推理全部由 Python 侧的
// FastAPI 桥（项目根目录 server/app.py）承担，Tauri 前端仅通过 fetch 调用
// http://localhost:8000 的 /api/query 等端点。因此 Rust 侧保持最小化：
// 不封装任何 Command，交给 WebView 直接发 HTTP 请求。
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
