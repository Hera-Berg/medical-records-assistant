import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { setupApi } from "./api";
import { Setup } from "./components/Setup";
import { rememberInstallation } from "./installation";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("no #root element in the page");
const mount = createRoot(root);

/*
 * The desktop app serves a setup page until there is a record to open, from the
 * same address. Only the setup server answers `/api/setup`; the record's server
 * does not, and that is how the page knows which it is talking to.
 */
setupApi
  .state()
  .then((state) => {
    rememberInstallation(state.packaged);
    mount.render(
      <StrictMode>
        <Setup initial={state} />
      </StrictMode>,
    );
  })
  .catch(() => {
    mount.render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
  });
