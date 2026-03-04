"use client";

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const items = [
  ['/', 'Overview'],
  ['/login', 'Login'],
  ['/onboarding', 'Onboarding'],
  ['/billing', 'Billing'],
  ['/projects', 'Projects'],
  ['/projects/new', 'New'],
  ['/projects/demo', 'Timeline'],
];

export default function Nav() {
  const pathname = usePathname();

  return (
    <>
      <Link className="brandBlock" href="/">
        <h1 className="brandTitle">BestShotAI</h1>
        <p className="brandSub">AI video editor</p>
      </Link>

      <nav className="headerNav">
        {items.map(([href, label]) => {
          const active = href === pathname || (href.includes('#') && pathname === '/');
          return (
            <Link className={`chip ${active ? 'chipActive' : ''}`} key={href} href={href}>
              {label}
            </Link>
          );
        })}
      </nav>
    </>
  );
}
