export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return <main id="main" className="grid min-h-screen place-items-center p-4">{children}</main>;
}
