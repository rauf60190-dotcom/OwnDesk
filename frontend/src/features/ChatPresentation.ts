const formatters = new Map<string, { day: Intl.DateTimeFormat; clock: Intl.DateTimeFormat }>();

function forZone(timeZone: string) {
  let format = formatters.get(timeZone);
  if (!format) {
    format = {
      day: new Intl.DateTimeFormat("ru", { dateStyle: "long", timeZone }),
      clock: new Intl.DateTimeFormat("ru", { timeStyle: "short", timeZone }),
    };
    formatters.set(timeZone, format);
  }
  return format;
}

export function chatDay(value: string, timeZone: string) {
  const formatter = forZone(timeZone).day;
  const day = formatter.format(new Date(value));
  return day === formatter.format(new Date()) ? "Сегодня" : day;
}

export function chatClock(value: string, timeZone: string) {
  return forZone(timeZone).clock.format(new Date(value));
}

export function chatInitials(name: string) {
  return name.trim().split(/\s+/).slice(0, 2).map((word) => Array.from(word)[0] || "").join("").toLocaleUpperCase("ru") || "·";
}
