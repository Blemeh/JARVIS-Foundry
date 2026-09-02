import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@fontsource-variable/geist";
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./styles/tokens.css";
import "./styles/global.css";
import "./styles/shell.css";
import "./styles/components.css";
import "./styles/pages.css";
import "./styles/v3.css";
import "./styles/v4.css";
import "./styles/god-mode.css";
import "./styles/autonomy.css";
import "./components/jarvis-core/jarvis-core.css";
import { App } from "./App";
import { CommandCenterProvider } from "./context/CommandCenterContext";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <CommandCenterProvider>
        <App />
      </CommandCenterProvider>
    </BrowserRouter>
  </StrictMode>,
);
