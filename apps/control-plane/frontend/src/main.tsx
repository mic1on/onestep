import ReactDOM from "react-dom/client";

import { AppProviders } from "./app/providers";
import "./lib/i18n";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(<AppProviders />);
