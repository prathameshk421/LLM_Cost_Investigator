import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { installAuth } from "./auth";
import "./styles/tokens.css";
import "./styles/glass.css";

installAuth();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
