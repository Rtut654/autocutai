import './globals.css';
import Nav from '../components/Nav';
import Footer from '../components/Footer';

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="siteShell">
          <header className="siteHeader">
            <div className="headerInner">
              <Nav />
            </div>
          </header>
          <main className="main">{children}</main>
        </div>
        <Footer />
      </body>
    </html>
  );
}
