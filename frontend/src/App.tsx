import { PublicLogin as Login } from "./features/PublicLogin";
import { ProductDemo, ProductWelcome } from "./features/ProductWelcome";
import { Privacy } from "./features/Privacy";
import { Terms } from "./features/Terms";
import { CustomerSupport } from "./features/CustomerSupport";
import { PortalHome } from "./features/Installation";
import { AccountSecurity } from "./features/AccountSecurity";
import { useEffect, useState, createContext, useContext } from "react";
import {
  Routes,
  Route,
  NavLink,
  Navigate,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Headset,
  Inbox,
  Columns3,
  Users,
  BarChart3,
  Settings,
  ChevronDown,
  Bell,
  LogOut,
  Moon,
  Sun,
  Menu,
  ChevronRight,
  CircleHelp,
} from "lucide-react";
import { api, post, patch, queues, roles, type Me } from "./shared/api";
import { Loading, ErrorBox, Modal } from "./shared/ui";
import { TicketBoard } from "./features/TicketBoard";
import { TicketsPage } from "./features/Tickets";
import {
  CustomersPage,
  ReportsPage,
  SettingsPage,
  UntriagedPage,
} from "./features/Resources";

const UserContext = createContext<Me>(null!);
export const useMe = () => useContext(UserContext);

export function App() {
  const { pathname, hash, search } = useLocation();
  if (pathname === "/privacy" || pathname === "/privacy/") return <Privacy />;
  if (pathname === "/terms" || pathname === "/terms/") return <Terms />;
  if (pathname === "/welcome" || pathname === "/welcome/") return <ProductWelcome />;
  if (pathname === "/demo" || pathname === "/demo/") return <ProductDemo />;
  if (pathname === "/support") return <div className="customer-entry"><CustomerSupport /></div>;
  if (pathname === "/") {
    const params = new URLSearchParams(search);
    if (/^#(verify|invite|reset)=/.test(hash) || ["auth_error", "mfa", "signup"].some(key => params.has(key))) {
      return <Navigate to={{ pathname: "/login", hash, search }} replace />;
    }
    return <RootEntry />;
  }
  return <StaffApp />;
}

function RootEntry() {
  const options = useQuery({ queryKey: ["auth-options"], queryFn: () => api<{ deployment_mode: string }>("/auth/options") });
  const me = useQuery<Me>({ queryKey: ["me"], queryFn: () => api("/auth/me"),
    enabled: options.data?.deployment_mode === "portal" });
  if (options.isPending) return <Loading />;
  if (options.data?.deployment_mode === "portal") {
    return me.data ? <Navigate to="/cabinet" replace /> : <ProductWelcome />;
  }
  if (options.isError && window.location.hostname === "owndesk.work") return <ProductWelcome />;
  return <StaffApp />;
}

function StaffApp() {
  const [verifyPending, setVerifyPending] = useState(() => /^#(verify|invite|reset)=/.test(window.location.hash));
  const qc = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const me = useQuery<Me>({ queryKey: ["me"], queryFn: () => api("/auth/me") });
  const counts = useQuery<Record<string, number>>({
    queryKey: ["counts"],
    queryFn: () => api("/queues/counts"),
    enabled: !!me.data && me.data.deployment_mode !== "portal",
    refetchInterval: 15000,
  });
  const [expanded, setExpanded] = useState(true),
    [mobile, setMobile] = useState(false),
    [notifications, setNotifications] = useState(false),
    [error, setError] = useState<unknown>();
  const notes = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api("/notifications"),
    enabled: !!me.data && me.data.deployment_mode !== "portal",
    refetchInterval: 30000,
  });
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (me.data) {
      setExpanded(me.data.preferences.tickets_expanded !== false);
      document.documentElement.dataset.theme =
        me.data.preferences.theme || "light";
    }
  }, [me.data]);
  useEffect(() => {
    if (!me.data || me.data.deployment_mode === "portal") return;
    let ws: WebSocket;
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const seen = new Set<string>();
    const connect = () => {
      ws = new WebSocket(
        `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws/events`,
      );
      ws.onopen = () => {
        setConnected(true);
        qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      };
      ws.onmessage = (e) => {
        const event = JSON.parse(e.data);
        if (seen.has(event.id)) return;
        seen.add(event.id);
        if (seen.size > 1000) seen.clear();
        qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      };
      ws.onclose = (e) => {
        setConnected(false);
        if (e.code === 4403) {
          qc.invalidateQueries({ queryKey: ["me"] });
          return;
        }
        if (!stopped) timer = setTimeout(connect, 4000);
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [me.data?.id]);
  if (me.isPending) return <Loading />;
  if (!me.data || verifyPending)
    return (
      <Login
        onLogin={() => {
          setVerifyPending(false);
          qc.removeQueries({ predicate: q => q.queryKey[0] !== "me" });
          void me.refetch().then(result => {
            navigate(result.data?.deployment_mode === "portal" ? "/cabinet" : "/tickets", { replace: true });
          });
        }}
      />
    );
  if (me.data.deployment_mode === "portal") {
    if (location.pathname !== "/cabinet") return <Navigate to="/cabinet" replace />;
    return <PortalHome user={me.data} onLogout={()=>{qc.clear();navigate("/",{replace:true});}} />;
  }
  const user = me.data;
  const queue = new URLSearchParams(location.search).get("queue") || "active";
  const isTickets =
    location.pathname.startsWith("/tickets") ||
    location.pathname === "/untriaged";
  const savePreference = async (value: object) => {
    try {
      await patch("/auth/me", value);
      await qc.invalidateQueries({ queryKey: ["me"] });
    } catch (e) {
      setError(e);
    }
  };
  const closeMenu = () => setMobile(false);
  return (
    <UserContext.Provider value={user}>
      <div className={"shell " + (mobile ? "menu-open" : "")}>
        <aside className="sidebar">
          <NavLink className="brand" to="/tickets">
            <span className="brand-symbol">
              <Headset size={22} />
            </span>
            <strong>
              OwnDesk<span>ЦЕНТР ПОДДЕРЖКИ</span>
            </strong>
          </NavLink>
          <div className="nav-caption">РАБОТА</div>
          <nav>
            <button
              className={"nav-main " + (isTickets ? "selected" : "")}
              onClick={() => {
                setExpanded(!expanded);
                savePreference({ tickets_expanded: !expanded });
              }}
            >
              <Inbox size={19} />
              <span>Запросы</span>
              <ChevronDown size={16} className={expanded ? "" : "rotated"} />
            </button>
            {expanded && (
              <div className="subnav">
                {Object.entries(queues).map(([id, title]) => (
                  <NavLink
                    key={id}
                    onClick={closeMenu}
                    to={"/tickets?queue=" + id}
                    className={
                      isTickets &&
                      queue === id &&
                      location.pathname !== "/untriaged"
                        ? "current"
                        : ""
                    }
                  >
                    <span>{title}</span>
                    <span className="nav-count">
                      {counts.data?.[id] ?? "—"}
                    </span>
                  </NavLink>
                ))}
                <NavLink
                  to="/untriaged"
                  onClick={closeMenu}
                  className={
                    location.pathname === "/untriaged" ? "current" : ""
                  }
                >
                  <span>На разборе</span>
                  <span
                    className={
                      "nav-count " + (counts.data?.untriaged ? "attention" : "")
                    }
                  >
                    {counts.data?.untriaged ?? 0}
                  </span>
                </NavLink>
              </div>
            )}
            <NavLink className="nav-main" to="/board" onClick={closeMenu}>
              <Columns3 size={19} /><span>Доска тикетов</span>
            </NavLink>
            <NavLink className="nav-main" to="/customers" onClick={closeMenu}>
              <Users size={19} />
              <span>Клиенты</span>
            </NavLink>
            {["admin", "manager"].includes(user.role) && (
              <NavLink className="nav-main" to="/reports" onClick={closeMenu}>
                <BarChart3 size={19} />
                <span>Отчёты</span>
              </NavLink>
            )}
            {user.role === "admin" && (
              <NavLink className="nav-main" to="/settings" onClick={closeMenu}>
                <Settings size={19} />
                <span>Настройки</span>
              </NavLink>
            )}
          </nav>
          <div className="sidebar-bottom">
            <div className="connection">
              <i className={connected ? "online" : ""} />
              {connected ? "Обновления подключены" : "Обновление через API"}
            </div>
            <button className="profile" onClick={() => { navigate("/profile"); closeMenu(); }}>
              <span className="avatar">{user.name[0]}</span>
              <span>
                <strong>{user.name}</strong>
                <small>{roles[user.role]}</small>
              </span>
              <ChevronRight size={16} />
            </button>
          </div>
        </aside>
        {mobile && (
          <button
            className="menu-shade"
            aria-label="Закрыть меню"
            onClick={closeMenu}
          />
        )}
        <main>
          <header className="topbar">
            <div>
              <button
                className="icon-button mobile-menu"
                onClick={() => setMobile(!mobile)}
                aria-label="Меню"
              >
                <Menu size={20} />
              </button>
              <span title="Организация">{user.workspace.name}</span>
              <ChevronRight size={14} />
              <strong>
                {isTickets
                  ? "Запросы"
                  : location.pathname === "/board"
                    ? "Доска тикетов"
                    : location.pathname === "/customers"
                    ? "Клиенты"
                    : location.pathname === "/reports"
                      ? "Отчёты"
                      : location.pathname === "/profile"
                        ? "Профиль"
                        : "Настройки"}
              </strong>
            </div>
            <div>
              {user.development && (
                <span className="dev-label">Среда разработки</span>
              )}
              <button
                className="icon-button"
                aria-label="Переключить тему"
                onClick={() =>
                  savePreference({
                    theme: user.preferences.theme === "dark" ? "light" : "dark",
                  })
                }
              >
                {user.preferences.theme === "dark" ? (
                  <Sun size={19} />
                ) : (
                  <Moon size={19} />
                )}
              </button>
              <button
                className="icon-button notification-button"
                aria-label="Уведомления"
                onClick={() => setNotifications(true)}
              >
                <Bell size={20} />
                {notes.data?.results.some((n: any) => !n.read_at) && <i />}
              </button>
            </div>
          </header>
          <ErrorBox error={error} />
          <Routes>
            <Route path="/board" element={<TicketBoard />} />
            <Route path="/tickets" element={<TicketsPage />} />
            <Route path="/tickets/:id" element={<TicketsPage />} />
            <Route path="/untriaged" element={<UntriagedPage />} />
            <Route path="/customers" element={<CustomersPage />} />
            <Route path="/reports" element={<ReportsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route
              path="/profile"
              element={
                <div className="page">
                  <div className="page-heading">
                    <h1>Профиль сотрудника</h1>
                  </div>
                  <section className="card narrow">
                    <h2>{user.name}</h2>
                    <p>
                      {user.username} · {roles[user.role]}
                    </p>
                    <p className="muted">
                      Часовой пояс: {user.workspace.timezone}
                    </p>
                    <button
                      onClick={async () => {
                        await post("/auth/logout", {});
                        qc.clear();
                        navigate("/tickets");
                        window.location.reload();
                      }}
                    >
                      <LogOut size={16} />
                      Выйти из аккаунта
                    </button>
                  </section>
                  <AccountSecurity />
                </div>
              }
            />
            <Route path="*" element={<Navigate to="/tickets" replace />} />
          </Routes>
        </main>
        {notifications && (
          <Modal title="Уведомления" onClose={() => setNotifications(false)}>
            <div className="notification-list">
              {notes.data?.results.length ? (
                notes.data.results.map((n: any) => (
                  <button
                    key={n.id}
                    className={n.read_at ? "read" : ""}
                    onClick={async () => {
                      await post(`/notifications/${n.id}/read`, {});
                      qc.invalidateQueries({ queryKey: ["notifications"] });
                      if (n.ticket_id) {
                        navigate("/tickets/" + n.ticket_id);
                        setNotifications(false);
                      }
                    }}
                  >
                    <Bell size={17} />
                    <span>{n.text}</span>
                    {!n.read_at && <i />}
                  </button>
                ))
              ) : (
                <p className="muted">Новых уведомлений нет.</p>
              )}
            </div>
          </Modal>
        )}
      </div>
    </UserContext.Provider>
  );
}
