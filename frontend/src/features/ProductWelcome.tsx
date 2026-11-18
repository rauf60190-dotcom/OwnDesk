import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDown, ArrowRight, ArrowUpRight, ChartNoAxesColumn, Check, CircleHelp, Columns3, Headset,
  LockKeyhole, Maximize2, MessageSquare, Paperclip, Search, Send, Server,
} from "lucide-react";
import { api } from "../shared/api";
import { PointerSurface } from "./PointerSurface";
import { WelcomeReportDemo } from "./WelcomeReportDemo";
import { WelcomeBoardDemo } from "./WelcomeBoardDemo";
import reportsCover from "../assets/demo-reports.jpg";

type Demo = "chat" | "support" | "board" | "reports";
type Signup = { deployment_mode: string; email_registration: boolean; google_login: boolean; public_registration: boolean };

const demoTabs = [
  { id: "chat", title: "Чаты", icon: MessageSquare },
  { id: "support", title: "Кабинет клиента", icon: CircleHelp },
  { id: "board", title: "Доска тикетов", icon: Columns3 },
  { id: "reports", title: "Отчёты", icon: ChartNoAxesColumn },
] as const;

const features = [
  { icon: Headset, title: "Service Desk", detail: "Ведите обращения в общей очереди. Добавляйте внутренние заметки и следите за статусами." },
  { icon: MessageSquare, title: "Чаты и кабинет клиента", detail: "Отвечайте клиентам, уточняйте детали и открывайте фото и видео прямо в обращении." },
  { icon: Columns3, title: "Тикеты разработчиков", detail: "Передавайте сложные задачи по цепочке L1 → L2 → L3 → разработка. История и вложения остаются в тикете." },
  { icon: LockKeyhole, title: "Роли и доступы", detail: "Приглашайте сотрудников по ссылке и назначайте им роли и команды." },
  { icon: Send, title: "Интеграция с Telegram", detail: "Подключите бота и отвечайте на сообщения из Telegram в рабочей очереди OwnDesk." },
  { icon: ChartNoAxesColumn, title: "Отчёты поддержки", detail: "Смотрите, сколько обращений создано, решено и закрыто за период и как команда соблюдает SLA." },
];

const channels = [
  ["Анна Волкова", "Не открывается отчёт", "После экспорта отчёт не открывается. Прикладываю снимок экрана.", "Уточните, пожалуйста, в каком браузере возникла ошибка."],
  ["Тимур Алиев", "Настройка уведомлений", "Где включить уведомления о новых обращениях?", "Откройте настройки профиля. Покажу, где находится нужный переключатель."],
  ["Мадина Рахимова", "Не пришёл код входа", "Запросила код дважды. На почте его пока нет.", "Проверьте папку «Спам». Уточните, пожалуйста, время последней попытки."],
  ["Алексей Петров", "Загрузка видео", "Видео прикрепляется, но потом исчезает из формы.", "Подскажите формат файла и его размер."],
  ["Елена Ким", "Добавление сотрудника", "Могу ли я выдать доступ только к одной команде?", "Да, при создании приглашения выберите команду и роль."],
  ["Сарвар Юсупов", "Поиск обращения", "Где найти обращение, которое вчера закрыли?", "Откройте все запросы и введите номер в поиск."],
  ["Олег Смирнов", "Изменение реквизитов", "Как обновить название нашей компании?", "Передам запрос администратору вашей организации."],
  ["София Иванова", "Не приходит ответ в Telegram", "В кабинете вижу ответ, а в Telegram сообщения нет.", "Проверяем статус доставки. Пришлите номер обращения."],
  ["Руслан Ахмедов", "Доступ к отчётам", "После смены роли пропал раздел «Отчёты».", "Доступ к отчётам зависит от роли. Уточним права с администратором."],
  ["Ирина Белова", "Приглашение истекло", "Ссылка из письма больше не открывается.", "Попросите администратора выдать новое приглашение."],
  ["Азиз Каримов", "Отправка фотографии", "Можно приложить несколько фотографий к заявке?", "Да. Добавьте файлы к обращению и дождитесь загрузки."],
  ["Мария Павлова", "Смена часового пояса", "В отчёте время закрытия отличается от моего.", "Отчёты используют часовой пояс рабочего пространства."],
].map(([customer, title, question, reply], index) => ({
  customer, title, question, reply,
  initials: customer.split(" ").map(part => part[0]).join(""),
  source: index % 3 === 0 ? "Кабинет клиента" : "Telegram",
  time: `10:${String(48 - index * 3).padStart(2, "0")}`,
}));

function DemoAttachment() {
  const dialog = useRef<HTMLDialogElement>(null);
  const [zoom, setZoom] = useState(false);
  return <>
    <button type="button" className="welcome-demo-file" onClick={() => dialog.current?.showModal()}>
      <Paperclip size={14} /> Открыть отчёт.jpg
    </button>
    <dialog ref={dialog} className="welcome-attachment-dialog" aria-label="Просмотр вложения"
      onClose={() => setZoom(false)}>
      <header>
        <strong>отчёт.jpg</strong>
        <button type="button" onClick={() => setZoom(value => !value)}>{zoom ? "Уменьшить" : "Увеличить"}</button>
        <button type="button" onClick={() => dialog.current?.close()}>Закрыть</button>
      </header>
      <div className="welcome-attachment-image">
        <img className={zoom ? "zoomed" : undefined} src={reportsCover} width={880} height={880}
          alt="Пример отчёта OwnDesk с графиком закрытых заявок" />
      </div>
    </dialog>
  </>;
}

function ChatDemo() {
  const [channel, setChannel] = useState(0);
  const [search, setSearch] = useState("");
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [replies, setReplies] = useState<Record<number, { text: string; time: string }[]>>({});
  const messages = useRef<HTMLDivElement>(null);
  const draft = drafts[channel] ?? "";
  const active = channels[channel];
  const query = search.trim().toLocaleLowerCase("ru");
  const visible = channels.map((item, index) => ({ ...item, index })).filter(item =>
    `${item.customer} ${item.title}`.toLocaleLowerCase("ru").includes(query),
  );
  useEffect(() => {
    if (messages.current) messages.current.scrollTop = messages.current.scrollHeight;
  }, [channel, replies]);

  function send(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim()) return;
    const message = { text: draft.trim(), time: new Intl.DateTimeFormat("ru", { timeStyle: "short" }).format(new Date()) };
    setReplies(previous => ({ ...previous, [channel]: [...(previous[channel] || []), message] }));
    setDrafts(previous => ({ ...previous, [channel]: "" }));
  }
  return <div className="welcome-chat-demo">
    <aside className="welcome-demo-queue">
      <h3>Чаты в очереди <small>{visible.length} из {channels.length}</small></h3>
      <label className="welcome-demo-search"><Search size={16} /><input type="search" aria-label="Поиск чатов в демо"
        value={search} placeholder="Имя клиента или тема" onChange={event => setSearch(event.target.value)} /></label>
      <div className="welcome-demo-queue-list">
        {visible.map(item => <button key={item.customer} type="button" aria-pressed={channel === item.index}
          onClick={() => setChannel(item.index)}>
          <span className="welcome-demo-avatar">{item.initials}</span>
          <span><strong>{item.customer}</strong><small>{item.title}</small></span><time>{item.time}</time>
        </button>)}
        {!visible.length && <p className="welcome-demo-empty">Чаты не найдены</p>}
      </div>
    </aside>
    <div className="welcome-demo-thread">
      <header><MessageSquare size={18} /> <div><h3>{active.title}</h3><small>{active.customer} · {active.source}</small></div></header>
      <div className="welcome-demo-messages" ref={messages} aria-live="polite">
        <small className="welcome-demo-date">Сегодня</small>
        <article><strong>{active.customer}</strong> <time>{active.time}</time><p>{active.question}</p>{channel === 0 && <DemoAttachment />}</article>
        <article className="welcome-demo-reply"><strong>Поддержка</strong><p>{active.reply}</p></article>
        {channel === 0 && <article><strong>{active.customer}</strong><p>В Firefox. В Chrome этот же отчёт открывается.</p></article>}
        {replies[channel]?.map((reply, index) => <article key={index} className="welcome-demo-reply">
          <strong>Вы</strong> <time>{reply.time}</time><p>{reply.text}</p>
        </article>)}
      </div>
      <form className="welcome-demo-compose" onSubmit={send}>
        <label htmlFor="welcome-chat-reply">Ответ клиенту</label>
        <textarea id="welcome-chat-reply" aria-label="Тестовый ответ" value={draft}
          onChange={event => {
            const value = event.target.value;
            setDrafts(previous => ({ ...previous, [channel]: value }));
          }}
          maxLength={20000} rows={3} placeholder={`Напишите ответ для клиента ${active.customer}`} />
        <div><small>Сообщение появится в этой демо-переписке.</small>
          <button type="submit" disabled={!draft.trim()} aria-label="Отправить тестовый ответ"><Send size={16} /> Отправить</button></div>
      </form>
    </div>
  </div>;
}

function SupportDemo() {
  const [details, setDetails] = useState(false);
  return <div className="welcome-support-demo">
    <div className="welcome-support-card">
      <span className="welcome-demo-state"><span /> В работе</span>
      <h3>№24 · Не открывается отчёт</h3>
      <p>Анна, сегодня в 10:24</p>
      <div className="welcome-demo-messages" aria-live="polite">
        <article><strong>Вы</strong><p>Отчёт за октябрь не открывается. Прикладываю скриншот.</p><DemoAttachment /></article>
        <article className="welcome-demo-reply"><strong>Поддержка</strong><p>Проверяем. Подскажите, в каком браузере вы открывали отчёт.</p></article>
        {details && <article><strong>Вы</strong><p>Открывала в Firefox. В других браузерах отчёт загружается.</p></article>}
      </div>
      <button type="button" onClick={() => setDetails(!details)}>{details ? "Сбросить пример" : "Добавить пример уточнения"} <ArrowRight size={15} /></button>
    </div>
    <aside className="welcome-demo-aside">
      <span className="welcome-demo-icon"><LockKeyhole size={22} /></span>
      <h3>Личный кабинет</h3>
      <p>Клиент открывает персональную ссылку и видит свои веб-обращения, ответы и вложения.</p>
    </aside>
  </div>;
}

function SignupAction({ options, secondary = false }: { options?: Signup; secondary?: boolean }) {
  const className = `welcome-button welcome-button-${secondary ? "outline" : "primary"}`;
  if (options?.deployment_mode === "portal" && options.public_registration) {
    if (options.email_registration) return <Link className={className} to="/login?signup=1">Создать аккаунт <ArrowRight size={17} /></Link>;
    if (options.google_login) return <a className={className} href="/api/v1/auth/google/start">Создать аккаунт через Google <ArrowRight size={17} /></a>;
  }
  return <Link className={className} to="/login">Открыть вход <ArrowRight size={17} /></Link>;
}

function WelcomeWorkspace({ fullscreen = false }: { fullscreen?: boolean }) {
  const [demo, setDemo] = useState<Demo>("chat");
  const [session, setSession] = useState(0);
  return <div className="welcome-demo" id="demo-interactive">
    <div className="welcome-demo-topbar">
      <div className="welcome-demo-tabs" role="group" aria-label="Экраны демо">
        {demoTabs.map(({ id, title, icon: Icon }) => <button key={id} type="button"
          aria-pressed={demo === id} onClick={() => setDemo(id)}><Icon size={16} /> {title}</button>)}
      </div>
      <div className="welcome-demo-tools">
        <button type="button" onClick={() => setSession(previous => previous + 1)}>Сбросить демо</button>
        {!fullscreen && <a className="welcome-button welcome-button-outline" href="/demo">На весь экран <Maximize2 size={16} /></a>}
      </div>
    </div>
    <div key={session}>
      <div hidden={demo !== "chat"}><ChatDemo /></div>
      <div hidden={demo !== "support"}><SupportDemo /></div>
      <div hidden={demo !== "board"}><WelcomeBoardDemo /></div>
      <div hidden={demo !== "reports"}><WelcomeReportDemo /></div>
    </div>
  </div>;
}

export function ProductDemo() {
  return <main className="product-welcome product-demo-page">
    <header className="welcome-demo-page-header">
      <div className="welcome-brand"><span><Headset size={19} /></span><h1>Демо OwnDesk</h1></div>
      <p>Рабочее пространство с вымышленными данными</p>
      <a className="welcome-button welcome-button-outline" href="/welcome#demo">Вернуться к витрине</a>
    </header>
    <WelcomeWorkspace fullscreen />
  </main>;
}

export function ProductWelcome() {
  const options = useQuery({ queryKey: ["auth-options"], queryFn: () => api<Signup>("/auth/options") });
  const available = options.data?.deployment_mode === "portal" && options.data.public_registration &&
    (options.data.email_registration || options.data.google_login);
  return <main className="product-welcome" id="top">
    <header className="welcome-header">
      <a className="welcome-brand" href="#top"><span><Headset size={19} /></span>OwnDesk</a>
      <nav aria-label="О продукте"><a href="#features">Возможности</a><a href="#demo">Демо</a><a href="#install">Установка</a></nav>
      <Link className="welcome-button welcome-button-outline" to="/login">Войти <ArrowUpRight size={16} /></Link>
    </header>

    <section className="welcome-hero">
      <PointerSurface>
        <div className="welcome-hero-inner">
          <div className="welcome-hero-copy">
            <span className="welcome-kicker"><span /> Бесплатное приложение для поддержки</span>
            <h1>Поддержка клиентов и тикеты разработчиков <em>в&nbsp;одном приложении.</em></h1>
            <p>Примите обращение из Telegram или веб-кабинета, передайте задачу разработчикам и отследите результат в отчётах. История и вложения хранятся на сервере вашей организации.</p>
            <div className="welcome-actions">
              <a className="welcome-button welcome-button-primary" href="#demo">Посмотреть демо <ArrowDown size={17} /></a>
              <SignupAction options={options.data} secondary />
            </div>
            {!options.isPending && !available && <small className="welcome-signup-note">
              {options.data?.deployment_mode === "organization" ? "Сотрудники получают доступ по приглашению." : "Регистрация на этом портале пока закрыта."}
            </small>}
          </div>
          <div className="welcome-product-cover" aria-hidden="true">
            <div className="welcome-cover-bar"><Headset size={18} /><strong>OwnDesk</strong><small>Демо-организация</small></div>
            <div className="welcome-cover-body">
              <aside><span className="welcome-cover-active"><MessageSquare size={15} /> Чаты</span><span><Columns3 size={15} /> Тикеты</span><span><ChartNoAxesColumn size={15} /> Отчёты</span></aside>
              <div className="welcome-cover-main">
                <div className="welcome-cover-chat">
                  <small><Send size={12} /> Telegram · 10:24</small>
                  <strong>Не открывается отчёт</strong>
                  <p>После экспорта файл не открывается.</p>
                  <span><Paperclip size={13} /> скриншот.png</span>
                </div>
                <div className="welcome-cover-ticket">
                  <small>Тикет №24 <ArrowUpRight size={13} /></small>
                  <strong>Проверить экспорт отчёта</strong>
                  <span>У разработчиков · В работе</span>
                </div>
                <div className="welcome-cover-metrics">
                  <div><small>Создано</small><strong>18</strong></div>
                  <div><small>Решено</small><strong>14</strong></div>
                  <div><small>Закрыто</small><strong>12</strong></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </PointerSurface>
    </section>

    <section className="welcome-section welcome-features" id="features">
      <div className="welcome-section-heading">
        <span className="welcome-kicker">Возможности OwnDesk</span>
        <h2>Что умеет OwnDesk</h2>
        <p>Клиент пишет в поддержку, команда ведёт обращение, разработчики получают задачу с контекстом.</p>
      </div>
      <div className="welcome-feature-grid">
        {features.map(({ icon: Icon, title, detail }) => <article key={title}>
          <span className="welcome-feature-icon"><Icon size={21} aria-hidden="true" /></span>
          <h3>{title}</h3>
          <p>{detail}</p>
        </article>)}
      </div>
    </section>

    <section className="welcome-section welcome-demo-section" id="demo">
      <div className="welcome-section-heading">
        <span className="welcome-kicker">Интерактивное демо</span>
        <h2>От первого сообщения до решения</h2>
        <p>Откройте один из 12 клиентских чатов, передайте задачу на доске из 36 тикетов или разберите отчёт по 600 закрытым заявкам. Все данные вымышлены.</p>
      </div>
      <WelcomeWorkspace />
    </section>

    <section className="welcome-section welcome-install" id="install">
      <div className="welcome-section-heading">
        <span className="welcome-kicker"><Server size={16} /> Самостоятельная установка</span>
        <h2>Запустите OwnDesk у себя</h2>
        <p>Кабинет владельца выдаёт дистрибутив. Рабочие обращения хранятся на сервере вашей организации.</p>
      </div>
      <ol className="welcome-steps">
        <li><span>01</span><h3>Получите дистрибутив</h3><p>Войдите в кабинет владельца и скачайте архив с инструкцией и контрольной суммой.</p></li>
        <li><span>02</span><h3>Запустите установку</h3><p>Используйте Windows для первого знакомства или Linux с Docker для серверного развёртывания.</p></li>
        <li><span>03</span><h3>Пригласите команду</h3><p>Создайте организацию, подключите Telegram и выдайте сотрудникам персональные приглашения.</p></li>
      </ol>
      <div className="welcome-install-action"><SignupAction options={options.data} /><span><Check size={16} /> Приложение бесплатное</span></div>
    </section>

    <footer className="welcome-footer">
      <a className="welcome-brand" href="#top"><span><Headset size={18} /></span>OwnDesk</a>
      <p>Поддержка клиентов на вашем сервере</p>
      <Link to="/privacy">Конфиденциальность</Link>
      <Link to="/terms">Правила использования</Link>
      <a href="#top">Наверх ↑</a>
    </footer>
  </main>;
}
