import { PointerSurface } from "./PointerSurface";

function Intro() {
  return <div className="registration-intro">
    <span className="registration-label">OwnDesk · бесплатное приложение</span>
    <h1>Поддержка клиентов на вашем сервере</h1>
    <p>Принимайте сообщения из Telegram, распределяйте обращения по командам и сохраняйте историю переписки.</p>
    <ul>
      <li>Кабинет владельца и приглашения сотрудников</li>
      <li>Фото, видео и внутренние заметки в обращениях</li>
      <li>Данные хранятся на сервере вашей организации</li>
    </ul>
  </div>;
}

export function RegistrationShowcase() {
  return <aside className="registration-showcase" aria-label="О продукте OwnDesk">
    <PointerSurface><Intro /></PointerSurface>
  </aside>;
}
