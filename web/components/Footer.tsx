import Link from 'next/link';

const links = [
  ['/privacy-policy', 'Privacy'],
  ['/terms-of-use', 'Terms'],
  ['/pricing', 'Pricing'],
  ['/login', 'Login'],
] as const;

export default function Footer() {
  return (
    <footer className="siteFooter">
      <div className="footerInner">
        <div>
          <h3>BestShotAI</h3>
          <p>AI-powered video editing for creators.</p>
          <p>Auto pre-edit, subtitle overlays, and smart insertion cues.</p>
        </div>
        <ul>
          {links.map(([href, label]) => (
            <li key={label}>
              <Link href={href}>{label}</Link>
            </li>
          ))}
        </ul>
      </div>
      <div className="footerBottom">
        <span>© 2026 BestShotAI. All rights reserved.</span>
        <span>Web · iOS · API</span>
      </div>
    </footer>
  );
}
