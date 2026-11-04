import type { CSSProperties } from 'react';

const paths = {
  logo: <path d="m5 18 7-13 7 13M8 12h8" />,
  workspace: <><rect x="4" y="4" width="16" height="16" rx="3" /><path d="m10 8 5 4-5 4Z" /></>,
  history: <><path d="M4 11a8 8 0 1 1 2 6M4 5v6h6M12 7v5l3 2" /></>,
  profiles: <><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M8 3v4m8-4v4M4 10h16" /></>,
  connections: <path d="m9 15 6-6m-7 3-3 3a3 3 0 0 0 4 4l3-3M12 8l3-3a3 3 0 0 1 4 4l-3 3" />,
  settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="3" /><circle cx="15" cy="17" r="3" /></>,
  monitor: <><rect x="3" y="4" width="18" height="14" rx="2" /><path d="M8 21h8m-4-3v3" /></>,
  stop: <rect x="6" y="6" width="12" height="12" rx="1" />,
  play: <path d="m8 5 11 7-11 7Z" />,
  check: <path d="m5 12 4 4L19 6" />,
  clock: <><circle cx="12" cy="12" r="8" /><path d="M12 7v5l3 2" /></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 6 9 7 9-7" /></>,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-10h.01" /></>,
  arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
  empty: <><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M9 8h6M9 12h6M9 16h3" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></>,
} as const;
export type IconName = keyof typeof paths;
export function Icon({ name, className = '', style }: { name: IconName; className?: string; style?: CSSProperties }) {
  return <svg className={`icon ${className}`} style={style} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
