/**
 * Appointments, worded for a person rather than a calendar.
 *
 * Dates arrive as calendar days in the record's own time zone (the server works
 * them out), so all arithmetic here is on whole days and nothing reads the
 * browser's clock or locale.
 */

import type { CalendarEvent } from "../types";

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function dayNumber(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Math.floor(Date.UTC(y!, m! - 1, d!) / 86_400_000);
}

const CANCELLED = new Set(["cancelled", "canceled"]);

/** The soonest booked appointment today or later, or null. */
export function nextAppointment(events: CalendarEvent[], today: string): CalendarEvent | null {
  const upcoming = events
    .filter(
      (event) =>
        event.kind === "appointment" &&
        !CANCELLED.has((event.status ?? "").toLowerCase()) &&
        dayNumber(event.date) >= dayNumber(today),
    )
    .sort((a, b) => (a.date + (a.time ?? "99")).localeCompare(b.date + (b.time ?? "99")));
  return upcoming[0] ?? null;
}

/** "9:30 am", from "09:30". */
export function clockTime(time: string | null | undefined): string | null {
  if (!time) return null;
  const match = /^(\d{1,2}):(\d{2})/.exec(time);
  if (!match) return time;
  const hour = Number(match[1]);
  const suffix = hour < 12 ? "am" : "pm";
  const twelve = hour % 12 === 0 ? 12 : hour % 12;
  return `${twelve}:${match[2]} ${suffix}`;
}

/** "Today", "Tomorrow", or "Tue 3 Nov". */
export function relativeDay(date: string, today: string): string {
  const gap = dayNumber(date) - dayNumber(today);
  if (gap === 0) return "Today";
  if (gap === 1) return "Tomorrow";
  const [y, m, d] = date.split("-").map(Number);
  const weekday = WEEKDAYS[new Date(Date.UTC(y!, m! - 1, d!)).getUTCDay()]!.slice(0, 3);
  return `${weekday} ${d} ${MONTHS[m! - 1]}`;
}

/** "Tomorrow · 9:30 am · Dr James Smith" */
export function appointmentWhen(event: CalendarEvent, today: string): string {
  return [relativeDay(event.date, today), clockTime(event.time), event.professional]
    .filter(Boolean)
    .join(" · ");
}
