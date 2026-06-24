import Link from 'next/link';
import { ArrowUpRight } from 'lucide-react';
import { Card, CardBody } from './ui/Card';
import { Badge } from './ui/Badge';
import type { Mentor } from '../lib/types';

const CATEGORY_LABELS: Record<string, string> = {
  job_career: 'Career',
  study_abroad: 'Study',
  visa_pr: 'Visa & PR',
  life_settling: 'Life abroad',
};

export function MentorCard({ mentor }: { mentor: Mentor }) {
  return (
    <Link href={`/mentors/${mentor.slug}`} className="group">
      <Card className="h-full hover:border-brand-300 hover:-translate-y-0.5 cursor-pointer">
        <CardBody className="pt-6 flex flex-col gap-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-brand-900 group-hover:text-brand-700">
                {mentor.display_name}
              </h3>
              {mentor.headline && (
                <p className="text-sm text-muted line-clamp-2 mt-1">{mentor.headline}</p>
              )}
            </div>
            <ArrowUpRight className="h-4 w-4 text-muted shrink-0 group-hover:text-accent-500 group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
          </div>

          <div className="flex flex-wrap gap-1.5">
            {mentor.expertise_country_codes.slice(0, 3).map((c) => (
              <Badge key={c} tone="brand">{c}</Badge>
            ))}
            {mentor.expertise_categories.slice(0, 2).map((cat) => (
              <Badge key={cat} tone="accent">{CATEGORY_LABELS[cat] ?? cat}</Badge>
            ))}
          </div>
        </CardBody>
      </Card>
    </Link>
  );
}
