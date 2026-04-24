"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import AccountMenu from "./AccountMenu";

const baseItems: Array<[string, string]> = [
  ["/", "Home"],
  ["/pricing", "Pricing"],
  ["/projects", "Projects"],
];

export default function Nav() {
  const pathname = usePathname();

  const isActive = (href: string) => {
    if (href === "/") return pathname === "/";
    if (href === "/projects") return pathname === "/projects" || pathname?.startsWith("/projects/");
    return pathname === href;
  };

  return (
    <>
      <div className="headerBrandWrap">
        <Link className="brandBlock" href="/">
          <h1 className="brandTitle">BestShotAI</h1>
          <p className="brandSub">AI video editor</p>
        </Link>
      </div>

      <nav className="headerNav" aria-label="Primary">
        {baseItems.map(([href, label]) => (
          <Link className={`chip ${isActive(href) ? "chipActive" : ""}`} key={href} href={href}>
            {label}
          </Link>
        ))}
      </nav>

      <div className="headerActions">
        <AccountMenu />
      </div>
    </>
  );
}
