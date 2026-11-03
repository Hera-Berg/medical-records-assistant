/**
 * Icons for the patient and carer screens, drawn here rather than fetched.
 *
 * Every one is decorative: it always sits beside its own word, carries
 * `aria-hidden`, and is never asked to mean anything by itself.
 */

type Props = { size?: number };

function Svg({ size = 20, children }: Props & { children: React.ReactNode }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

export const HomeIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M3.5 10.5 12 4l8.5 6.5V20a1 1 0 0 1-1 1H15v-6H9v6H4.5a1 1 0 0 1-1-1z" />
  </Svg>
);

export const PillIcon = (p: Props) => (
  <Svg {...p}>
    <rect x="3" y="8.5" width="18" height="7" rx="3.5" />
    <path d="M12 8.5v7" />
  </Svg>
);

export const HistoryIcon = (p: Props) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Svg>
);

export const CalendarIcon = (p: Props) => (
  <Svg {...p}>
    <rect x="3.5" y="5" width="17" height="15.5" rx="2" />
    <path d="M8 3v4M16 3v4M3.5 10h17" />
  </Svg>
);

export const AskIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M5 5h14a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 19 17h-7l-4.5 3.5V17H5a1.5 1.5 0 0 1-1.5-1.5v-9A1.5 1.5 0 0 1 5 5z" />
    <path d="M10 9.4a2 2 0 1 1 2.4 2c-.3.1-.4.3-.4.6v.4M12 14.6h.01" />
  </Svg>
);

export const MoreIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M5.5 12h.01M12 12h.01M18.5 12h.01" strokeWidth="3" />
  </Svg>
);

export const PlusIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

export const CameraIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M4 8h3l1.5-2.5h7L17 8h3a1 1 0 0 1 1 1v9.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z" />
    <circle cx="12" cy="13.5" r="3.5" />
  </Svg>
);

export const FileIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M6.5 3h7.5l4.5 4.5V20a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
    <path d="M14 3v4.5h4.5" />
  </Svg>
);

export const MicIcon = (p: Props) => (
  <Svg {...p}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" />
  </Svg>
);

export const PencilIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M4 20l1-4.5L15.5 5a2.1 2.1 0 0 1 3 3L8 18.5z" />
    <path d="M13.5 7l3 3" />
  </Svg>
);

export const ChevronRight = (p: Props) => (
  <Svg size={p.size ?? 18}>
    <path d="m9 6 6 6-6 6" />
  </Svg>
);

export const ChevronLeft = (p: Props) => (
  <Svg size={p.size ?? 18}>
    <path d="m15 6-6 6 6 6" />
  </Svg>
);

export const CloseIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
);

export const ClipIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M20 11.5 12.2 19.3a5 5 0 0 1-7.1-7.1l8.1-8.1a3.3 3.3 0 0 1 4.7 4.7l-8 8a1.7 1.7 0 0 1-2.4-2.4l7.3-7.3" />
  </Svg>
);

export const SendIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M12 19V5M6 11l6-6 6 6" />
  </Svg>
);

export const CheckIcon = (p: Props) => (
  <Svg size={p.size ?? 16}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Svg>
);

export const PrintIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M7 8V3.5h10V8M7 17H4.5a1 1 0 0 1-1-1v-6a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2v6a1 1 0 0 1-1 1H17" />
    <rect x="7" y="13.5" width="10" height="7" rx="1" />
  </Svg>
);
