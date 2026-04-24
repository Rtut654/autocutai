"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  addSessionListener,
  clearLastProjectId,
  clearStoredSession,
  getStoredSession,
} from "../lib/session";
import { api } from "../lib/api";
import type { User } from "../lib/types";

function isFakeEmail(email?: string | null): boolean {
  return Boolean(email && email.endsWith("@local.bestshotai"));
}

function displayName(user: User | null): string {
  if (user?.full_name?.trim()) return user.full_name.trim();
  if (user?.email && !isFakeEmail(user.email)) {
    const local = user.email.split("@")[0] || "";
    return local.charAt(0).toUpperCase() + local.slice(1);
  }
  if (user?.provider === "google") return "Google User";
  if (user?.provider === "apple") return "Apple User";
  return "User";
}

function displayEmail(user: User | null): string | null {
  if (!user?.email || isFakeEmail(user.email)) return null;
  return user.email;
}

function initialsForUser(user: User | null): string {
  const source = displayName(user);
  const parts = source.split(/[\s@._-]+/).filter(Boolean);
  return parts.slice(0, 2).map((part) => part[0]?.toUpperCase() || "").join("") || "U";
}

function providerLabel(provider?: string | null): string {
  if (provider === "google") return "Google";
  if (provider === "apple") return "Apple";
  return "Email";
}

export default function AccountMenu() {
  const pathname = usePathname();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [avatarBroken, setAvatarBroken] = useState(false);

  useEffect(() => {
    const sync = () => setUser(getStoredSession()?.user || null);
    sync();
    return addSessionListener(sync);
  }, []);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    setAvatarBroken(false);
  }, [user?.picture]);

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  const initials = useMemo(() => initialsForUser(user), [user]);
  const showImage = Boolean(user?.picture && !avatarBroken);

  // Not logged in — show a simple Login button instead of avatar
  if (!user) {
    return (
      <div className="accountMenu">
        <Link href="/login" className="accountLoginBtn">Login</Link>
      </div>
    );
  }

  return (
    <div className="accountMenu" ref={containerRef}>
      <button
        type="button"
        className={`avatarButton ${open ? "avatarButtonOpen" : ""}`}
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Open account menu"
      >
        {showImage ? (
          <img
            src={user.picture || ""}
            alt={displayName(user) || "Profile"}
            className="avatarImage"
            referrerPolicy="no-referrer"
            crossOrigin="anonymous"
            onError={() => setAvatarBroken(true)}
          />
        ) : (
          <span className="avatarFallback">{initials}</span>
        )}
      </button>

      {open ? (
        <div className="accountPopover" role="menu">
          <div className="accountPopoverHead">
            <div className="accountHero">
              {showImage ? (
                <img
                  src={user.picture || ""}
                  alt={displayName(user)}
                  className="accountHeroImage"
                  referrerPolicy="no-referrer"
                  crossOrigin="anonymous"
                  onError={() => setAvatarBroken(true)}
                />
              ) : (
                <span className="accountHeroFallback">{initials}</span>
              )}
              <div className="accountHeroText">
                <strong>{displayName(user)}</strong>
                {displayEmail(user) ? (
                  <span>{displayEmail(user)}</span>
                ) : (
                  <span>{providerLabel(user.provider)} account</span>
                )}
              </div>
            </div>
            <span className="accountProvider">{providerLabel(user.provider)}</span>
          </div>

          <div className="accountPopoverActions">
            <Link href="/" className="accountAction">Home</Link>
            <Link href="/profile" className="accountAction">Profile</Link>
            <Link href="/pricing" className="accountAction accountActionPrimary">Pricing</Link>
          </div>

          <div className="accountPopoverFooter">
            <Link href="/profile" className="accountDelete">Delete account</Link>
            <button
              type="button"
              className="accountLogout"
              onClick={async () => {
                const session = getStoredSession();
                try {
                  await api.logout(session?.access_token);
                } catch {
                  // Clear local state even if the backend session was already gone.
                }
                clearStoredSession();
                clearLastProjectId();
                window.location.assign("/login");
              }}
            >
              Logout
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
