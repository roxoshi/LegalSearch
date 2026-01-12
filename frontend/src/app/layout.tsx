import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css"; // Ensure this file exists for Tailwind

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Hybrid Search Engine",
  description: "Self-funded document search with FastAPI and pgvector",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${inter.className} bg-gray-50 text-slate-900 antialiased`}>
        {/* You can add a global Navbar here later */}
        <div className="min-h-screen">
          {children}
        </div>
      </body>
    </html>
  );
}