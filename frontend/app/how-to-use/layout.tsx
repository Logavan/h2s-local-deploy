import type { Metadata } from 'next';
import Script from 'next/script';

export const metadata: Metadata = {
  title: 'How to Use — HANACV2SQL | Step-by-Step Conversion Guides',
  description: 'Step-by-step guides on how to use HANACV2SQL: convert HANA Calculation Views to standard SQL with mapping metadata, then use the Mapping Engine to generate platform-specific SQL or PySpark.',
  alternates: {
    canonical: 'https://hanacv2sql.com/how-to-use',
  },
  openGraph: {
    title: 'How to Use — HANACV2SQL | Step-by-Step Conversion Guides',
    description: 'Step-by-step guides: upload XML, convert to SQL with mapping metadata, then generate platform SQL for Snowflake, BigQuery, Databricks & more.',
    url: 'https://hanacv2sql.com/how-to-use',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'How to Use — HANACV2SQL | Step-by-Step Conversion Guides',
    description: 'Step-by-step guides: upload XML, convert to SQL with mapping metadata, then generate platform SQL.',
  },
};

const faqs = [
  {
    question: 'What file formats are supported?',
    answer: 'Upload single .xml or .txt files, or a .zip containing multiple .xml/.txt files for bulk conversion.',
  },
  {
    question: 'How do I create a ZIP file for bulk conversion?',
    answer: 'Select all your HANA XML files in File Explorer (Windows) or Finder (Mac), right-click, and choose Send to > Compressed (zipped) folder (Windows) or Compress (Mac). Rename the ZIP to something meaningful, then upload it — the tool extracts and processes all .xml/.txt files inside automatically. Subfolders are also supported.',
  },
  {
    question: 'Which SQL platforms are supported?',
    answer: 'Generated SQL is compatible with BigQuery, Snowflake, Databricks, Amazon Redshift, and Microsoft Fabric.',
  },
  {
    question: 'What does the mapping file do?',
    answer: 'The encrypted Excel mapping file lets you map HANA table/column names to your target system\'s schema before generating final SQL.',
  },
  {
    question: 'Where are my conversions stored?',
    answer: 'All conversions are saved in your Account > Conversions page. You can re-download SQL files and mapping sheets anytime.',
  },
];

export default function HowToUseLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <Script
        id="how-to-use-faq-schema"
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            '@context': 'https://schema.org',
            '@type': 'FAQPage',
            mainEntity: faqs.map((faq) => ({
              '@type': 'Question',
              name: faq.question,
              acceptedAnswer: {
                '@type': 'Answer',
                text: faq.answer,
              },
            })),
          }),
        }}
      />
      {children}
    </>
  );
}
