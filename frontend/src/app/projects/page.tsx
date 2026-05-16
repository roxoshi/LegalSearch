'use client';

import { useState, useEffect } from 'react';
import {
  PlusIcon,
  FolderOpenIcon,
  TrashIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import {
  getProjects,
  saveProject,
  deleteProject,
  type Project,
} from '@/lib/projects';

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');

  useEffect(() => {
    setProjects(getProjects());
  }, []);

  const handleCreate = () => {
    if (!title.trim()) return;
    const project: Project = {
      id: crypto.randomUUID(),
      title: title.trim(),
      description: description.trim(),
      items: [],
      createdAt: new Date().toISOString(),
    };
    saveProject(project);
    setProjects(getProjects());
    setTitle('');
    setDescription('');
    setShowModal(false);
  };

  const handleDelete = (id: string) => {
    deleteProject(id);
    setProjects(getProjects());
  };

  const formatDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  };

  return (
    <div className="min-h-screen bg-[#f8f9fa] p-8">
      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Your Projects</h1>
          <p className="text-sm text-slate-400 mt-1">
            Organise your research and drafts by project
          </p>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
        >
          <PlusIcon className="w-4 h-4" />
          New Project
        </button>
      </div>

      {/* Project grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {projects.map((project) => (
          <ProjectCard
            key={project.id}
            project={project}
            onDelete={handleDelete}
            formatDate={formatDate}
          />
        ))}

        {/* Create new card */}
        <button
          onClick={() => setShowModal(true)}
          className="flex flex-col items-center justify-center gap-2 p-6 bg-white border-2 border-dashed border-slate-200
            rounded-xl text-slate-400 hover:border-blue-300 hover:text-blue-500 transition-colors min-h-[160px]"
        >
          <PlusIcon className="w-6 h-6" />
          <span className="text-sm font-medium">Create New Project</span>
        </button>
      </div>

      {/* New Project modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6">
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-lg font-semibold text-slate-900">New Project</h2>
              <button
                onClick={() => setShowModal(false)}
                className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
              >
                <XMarkIcon className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">
                  Project name
                </label>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                  placeholder="e.g. Input Tax Credit Disputes Q2"
                  autoFocus
                  className="w-full px-3 py-2.5 border border-slate-200 rounded-lg text-sm
                    focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-500 mb-1.5">
                  Description{' '}
                  <span className="text-slate-300 font-normal">(optional)</span>
                </label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Briefly describe this project..."
                  rows={3}
                  className="w-full px-3 py-2.5 border border-slate-200 rounded-lg text-sm resize-none
                    focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none"
                />
              </div>
            </div>

            <div className="flex gap-3 mt-6">
              <button
                onClick={() => setShowModal(false)}
                className="flex-1 py-2.5 text-sm font-medium text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={!title.trim()}
                className="flex-1 py-2.5 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700
                  disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                Create Project
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ProjectCard({
  project,
  onDelete,
  formatDate,
}: {
  project: Project;
  onDelete: (id: string) => void;
  formatDate: (iso: string) => string;
}) {
  return (
    <div className="group bg-white border border-slate-200 rounded-xl p-5 hover:border-slate-300 hover:shadow-sm transition-all flex flex-col">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="p-2 bg-blue-50 rounded-lg shrink-0">
            <FolderOpenIcon className="w-4 h-4 text-blue-600" />
          </div>
          <h3 className="font-semibold text-slate-900 text-sm truncate">
            {project.title}
          </h3>
        </div>
        <button
          onClick={() => onDelete(project.id)}
          className="p-1.5 text-slate-300 hover:text-red-400 hover:bg-red-50 rounded-lg transition-colors opacity-0 group-hover:opacity-100 shrink-0"
        >
          <TrashIcon className="w-4 h-4" />
        </button>
      </div>

      {project.description && (
        <p className="text-xs text-slate-400 mb-3 line-clamp-2 leading-relaxed">
          {project.description}
        </p>
      )}

      <div className="mt-auto flex items-center justify-between pt-3 border-t border-slate-100">
        <span className="text-xs text-slate-400">
          {project.items.length} item{project.items.length !== 1 ? 's' : ''}
        </span>
        <span className="text-xs text-slate-400">{formatDate(project.createdAt)}</span>
      </div>
    </div>
  );
}
