import Image from "next/image";

export function BugViperLogo({ size = 40 }: { size?: number }) {
  return (
    <Image
      src="/logo.svg"
      alt="BugViper"
      width={size}
      height={size}
    />
  );
}

export function BugViperFullLogo({ width = 300, height = 80 }: { width?: number; height?: number }) {
  return (
    <Image
      src="/full-logo.svg"
      alt="BugViper"
      width={width}
      height={height}
    />
  );
}
