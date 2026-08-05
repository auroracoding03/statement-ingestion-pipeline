import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { canWrite } from "./lib/dataSource";
import { Categories } from "./pages/Categories";
import { Merchants } from "./pages/Merchants";
import { Obligations } from "./pages/Obligations";
import { Overview } from "./pages/Overview";
import { Recurring } from "./pages/Recurring";
import { Review } from "./pages/Review";
import { Rules } from "./pages/Rules";
import { Transactions } from "./pages/Transactions";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HashRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="transactions" element={<Transactions />} />
          <Route path="categories" element={<Categories />} />
          <Route path="recurring" element={<Recurring />} />
          <Route path="merchants" element={<Merchants />} />
          {canWrite && <Route path="obligations" element={<Obligations />} />}
          {canWrite && <Route path="review" element={<Review />} />}
          {canWrite && <Route path="rules" element={<Rules />} />}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  </React.StrictMode>,
);
