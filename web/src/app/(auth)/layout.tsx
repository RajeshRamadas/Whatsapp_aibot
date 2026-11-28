import { AgencyBrandMark } from '@/components/agency-brand';
import { requestAgencyBrand } from '@/lib/agency-host';

export default async function AuthLayout({ children }: { children: React.ReactNode }) {
  const brand = await requestAgencyBrand();
  return <main id="main" className="grid min-h-screen place-items-center p-4">
    {brand ? <div className="w-full max-w-md space-y-4">
      <div className="text-center text-xl font-semibold"><AgencyBrandMark /></div>{children}
    </div> : children}
  </main>;
}
