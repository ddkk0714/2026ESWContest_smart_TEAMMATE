import 'dart:math' as math;

enum IrumaeMood { greeting, working, celebrating, sleeping, slumped }

const irumaeLabels = {
  IrumaeMood.greeting: '안녕',
  IrumaeMood.working: '집중 중',
  IrumaeMood.celebrating: '집중 완료',
  IrumaeMood.sleeping: '쉬는 중',
  IrumaeMood.slumped: '다음에 다시',
};

const irumaeCycleDurations = {
  IrumaeMood.greeting: Duration(milliseconds: 4800),
  IrumaeMood.working: Duration(milliseconds: 5800),
  IrumaeMood.celebrating: Duration(milliseconds: 3000),
  IrumaeMood.sleeping: Duration(milliseconds: 7200),
  IrumaeMood.slumped: Duration(milliseconds: 4000),
};

class IrumaePoseMotion {
  const IrumaePoseMotion(this.frame, this.dyFraction, this.angle, this.scaleY);
  final int frame;
  final double dyFraction;
  final double angle;
  final double scaleY;
}

/// Continuous, closed-loop motion keeps one crisp pose per state. Cross-fading
/// the source atlas keyframes would double faces because their outlines differ.
IrumaePoseMotion irumaePoseAt(IrumaeMood mood, double phase,
    {bool reducedMotion = false}) {
  final frame = switch (mood) {
    IrumaeMood.greeting => 1,
    IrumaeMood.celebrating => 2,
    _ => 0,
  };
  if (reducedMotion) return IrumaePoseMotion(frame, 0, 0, 1);
  final wave = math.sin((phase % 1) * math.pi * 2);
  final breath = (1 - math.cos((phase % 1) * math.pi * 2)) / 2;
  return switch (mood) {
    IrumaeMood.greeting =>
      IrumaePoseMotion(frame, -.006 * breath, .018 * wave, 1),
    IrumaeMood.working =>
      IrumaePoseMotion(frame, -.002 * breath, 0, 1 + .004 * breath),
    IrumaeMood.celebrating =>
      IrumaePoseMotion(frame, -.025 * breath, .015 * wave, 1),
    IrumaeMood.sleeping => IrumaePoseMotion(frame, 0, 0, 1 + .015 * breath),
    IrumaeMood.slumped =>
      IrumaePoseMotion(frame, .004 * breath, .008 * wave, 1),
  };
}
