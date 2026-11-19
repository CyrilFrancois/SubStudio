// layout.tsx — Root HTML shell: dark studio frame around every screen. Exports: metadata, default RootLayout.
import './globals.css';
import AppShell from '@/components/shell/AppShell';

export const metadata = {
  title: 'SubStudio',
  description: 'Subtitles in every language you want, for your whole video library',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#0a0a0a] text-slate-200 antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
