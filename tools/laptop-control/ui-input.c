#include <linux/uinput.h>
#include <sys/ioctl.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

static int emit(int fd, int type, int code, int value) {
  struct input_event e = {0}; e.type = type; e.code = code; e.value = value;
  return write(fd, &e, sizeof(e)) == sizeof(e) ? 0 : -1;
}
static void position(int fd, int x, int y) {
  emit(fd, EV_ABS, ABS_X, x); emit(fd, EV_ABS, ABS_Y, y);
  emit(fd, EV_SYN, SYN_REPORT, 0);
}
static int coordinate(const char *s, int max) {
  char *end; long value = strtol(s, &end, 10);
  return *s && !*end && value >= 0 && value <= max ? (int)value : -1;
}
int main(int argc, char **argv) {
  if (argc != 5) return 2;
  int x1=coordinate(argv[1],1023), y1=coordinate(argv[2],599);
  int x2=coordinate(argv[3],1023), y2=coordinate(argv[4],599);
  if (x1<0 || y1<0 || x2<0 || y2<0) return 2;
  int fd=open("/dev/uinput",O_WRONLY|O_NONBLOCK);
  if(fd<0) { perror("uinput"); return 1; }
  ioctl(fd, UI_SET_EVBIT, EV_KEY); ioctl(fd, UI_SET_KEYBIT, BTN_TOUCH);
  ioctl(fd, UI_SET_EVBIT, EV_ABS); ioctl(fd, UI_SET_ABSBIT, ABS_X);
  ioctl(fd, UI_SET_ABSBIT, ABS_Y); ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT);
  struct uinput_user_dev u={0};
  strcpy(u.name,"DESKMATE laptop touch"); u.id.bustype=BUS_VIRTUAL;
  u.absmax[ABS_X]=1024; u.absmax[ABS_Y]=600;
  if(write(fd,&u,sizeof(u))!=sizeof(u)||ioctl(fd,UI_DEV_CREATE)<0) { close(fd); return 1; }
  /* Weston needs time to discover the transient input device. */
  usleep(700000); position(fd,x1,y1); emit(fd,EV_KEY,BTN_TOUCH,1);
  emit(fd,EV_SYN,SYN_REPORT,0); usleep(80000);
  for(int i=1;i<=20;i++) { position(fd,x1+(x2-x1)*i/20,y1+(y2-y1)*i/20); usleep(12000); }
  emit(fd,EV_KEY,BTN_TOUCH,0); emit(fd,EV_SYN,SYN_REPORT,0);
  usleep(120000); ioctl(fd,UI_DEV_DESTROY); close(fd); return 0;
}
