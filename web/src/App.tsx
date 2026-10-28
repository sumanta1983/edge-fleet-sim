import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, NavLink, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { api, tokenStore, unwrap } from "./api/client";
import ConfigPage from "./pages/ConfigPage";
import DeviceDetail from "./pages/DeviceDetail";
import Fleet from "./pages/Fleet";
import Kpis from "./pages/Kpis";
import Login from "./pages/Login";
import Releases from "./pages/Releases";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => unwrap(api.GET("/auth/me")),
    staleTime: Infinity,
    enabled: !!tokenStore.get(),
  });
}

export function useIsOperator() {
  return useMe().data?.role === "operator";
}

function Layout() {
  const me = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  if (!tokenStore.get()) return <Navigate to="/login" replace />;

  function logout() {
    tokenStore.clear();
    queryClient.clear(); // don't show the next user this user's cached data
    navigate("/login");
  }

  return (
    <>
      <header className="topbar">
        <span className="brand">edgefleet-sim</span>
        <nav>
          <NavLink to="/" end>Fleet</NavLink>
          <NavLink to="/config">Config</NavLink>
          <NavLink to="/releases">Releases</NavLink>
          <NavLink to="/kpis">KPIs</NavLink>
        </nav>
        <span className="who">
          {me.data && (
            <>
              {me.data.email} · {me.data.tenant_name} · <em>{me.data.role}</em>
            </>
          )}
          <button className="link" onClick={logout}>Log out</button>
        </span>
      </header>
      <main>
        <Outlet />
      </main>
    </>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Layout />}>
        <Route index element={<Fleet />} />
        <Route path="devices/:deviceId" element={<DeviceDetail />} />
        <Route path="config" element={<ConfigPage />} />
        <Route path="releases" element={<Releases />} />
        <Route path="kpis" element={<Kpis />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
