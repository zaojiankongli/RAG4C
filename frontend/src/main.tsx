import React from "react";
import ReactDOM from "react-dom/client";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import "tdesign-react/es/style/index.css";
import AppProviders from "./AppProviders";
import "./styles.css";
import "./shell/enterprise-shell.css";

dayjs.locale("zh-cn");

const root = document.getElementById("root");
if (!root) throw new Error("缺少应用挂载节点 #root");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <AppProviders />
  </React.StrictMode>,
);
