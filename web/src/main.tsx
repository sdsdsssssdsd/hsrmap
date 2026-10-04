import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "leaflet/dist/leaflet.css";
import "./styles/tokens.css";
import "./styles/layout.css";
import { installNetworkGuard } from "./guard";
import { App } from "./app/App";
import { parseHash } from "./app/router";

installNetworkGuard();

const root = document.getElementById("root")!;
const route = parseHash();
if (route.debug) {
  root.innerHTML = `<div class="debug">Debug: 使用 <code>/#/map/&lt;id&gt;</code>。旧 map id 输入已移出主界面。</div>`;
} else {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
