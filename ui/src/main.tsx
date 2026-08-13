import React from "react";
import ReactDOM from "react-dom/client";

import { Layout } from "./components/Layout";
import { canWrite } from "./lib/dataSource";
import { useHashPath } from "./lib/router";
import { Budget } from "./pages/Budget";
import { Cards } from "./pages/Cards";
import { Categories } from "./pages/Categories";
import { AiAssistant } from "./pages/AiAssistant";
import { Ingestion } from "./pages/Ingestion";
import { Insights } from "./pages/Insights";
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
    "/ingestion": canWrite ? <Ingestion /> : <Overview />,
    "/transactions": <Transactions />,
    "/cards": <Cards />,
    "/categories": <Categories />,
    "/budget": canWrite ? <Budget /> : <Overview />,
    "/recurring": <Recurring />,
    "/merchants": <Merchants />,
    "/insights": canWrite ? <Insights /> : <Overview />,
    "/ai-assistant": canWrite ? <AiAssistant /> : <Overview />,
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
