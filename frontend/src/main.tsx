import { createRoot } from "react-dom/client";

import { App } from "./App";
import "@telegram-apps/telegram-ui/dist/styles.css";
import "./index.css";

createRoot(document.getElementById("root")!).render(<App />);
