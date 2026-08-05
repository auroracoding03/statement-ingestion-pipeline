import React from "react";
import ReactDOM from "react-dom/client";

import { Layout } from "./components/Layout";
import { canWrite } from "./lib/dataSource";
import { useHashPath } from "./lib/router";
import { Categories } from "./pages/Categories";
import { Merchants } from "./pages/Merchants";
import { Overview } from "./pages/Overview";
import { Recurring } from "./pages/Recurring";
import { Review } from "./pages/Review";
import { Rules } from "./pages/Rules";
import { Transactions } from "./pages/Transactions";
import "./styles.css";

function App() {
  const path = useHashPath();
  const page = {
    "/": <Overview />,
    "/transactions": <Transactions />,
    "/categories": <Categories />,
    "/recurring": <Recurring />,
    "/merchants": <Merchants />,
    "/review": canWrite ? <Review /> : <Overview />,
    "/rules": canWrite ? <Rules /> : <Overview />,
  }[path] ?? <Overview />;

  return <Layout>{page}</Layout>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
