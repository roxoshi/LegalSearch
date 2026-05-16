'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  MagnifyingGlassIcon,
  FolderIcon,
  UserCircleIcon,
} from '@heroicons/react/24/outline';
import {
  MagnifyingGlassIcon as MagnifyingGlassSolid,
  FolderIcon as FolderSolid,
} from '@heroicons/react/24/solid';
import { useAuth } from '@/context/AuthContext';

export function Sidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  const isSearch = pathname === '/';
  const isProjects = pathname.startsWith('/projects');

  return (
    <aside className="fixed left-0 top-0 h-full w-14 bg-white border-r border-slate-200 flex flex-col items-center py-4 z-20">
      {/* Logo */}
      <Link href="/" className="mb-6 shrink-0">
        <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center text-white font-bold text-sm select-none">
          T
        </div>
      </Link>

      {/* Nav icons */}
      <nav className="flex flex-col gap-1 flex-1">
        <Link
          href="/"
          title="Search"
          className={`p-2 rounded-lg transition-colors ${
            isSearch
              ? 'bg-blue-50 text-blue-600'
              : 'text-slate-400 hover:text-slate-600 hover:bg-slate-50'
          }`}
        >
          {isSearch ? (
            <MagnifyingGlassSolid className="w-5 h-5" />
          ) : (
            <MagnifyingGlassIcon className="w-5 h-5" />
          )}
        </Link>

        <Link
          href="/projects"
          title="Projects"
          className={`p-2 rounded-lg transition-colors ${
            isProjects
              ? 'bg-blue-50 text-blue-600'
              : 'text-slate-400 hover:text-slate-600 hover:bg-slate-50'
          }`}
        >
          {isProjects ? (
            <FolderSolid className="w-5 h-5" />
          ) : (
            <FolderIcon className="w-5 h-5" />
          )}
        </Link>

      </nav>

      {/* User / sign-in at bottom */}
      <div className="shrink-0">
        {user ? (
          <button
            onClick={() => logout()}
            title={`Logout (${user.first_name})`}
            className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-50 rounded-lg transition-colors"
          >
            <UserCircleIcon className="w-5 h-5" />
          </button>
        ) : (
          <Link
            href="/login"
            title="Sign In"
            className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-50 rounded-lg transition-colors block"
          >
            <UserCircleIcon className="w-5 h-5" />
          </Link>
        )}
      </div>
    </aside>
  );
}
