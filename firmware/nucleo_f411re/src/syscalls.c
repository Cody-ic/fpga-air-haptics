/* The protocol uses an explicit UART queue; libc must not write to that link. */
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/stat.h>
#include <sys/types.h>

extern char _heap_start, _heap_end;
extern void Default_Handler(void);
void *_sbrk(ptrdiff_t amount)
{
    static char *current;
    if (!current) current = &_heap_start;
    if (amount < 0 || (uintptr_t)amount > (uintptr_t)&_heap_end-(uintptr_t)current) {
        errno = ENOMEM; return (void *)-1;
    }
    char *previous = current; current += amount; return previous;
}
int _write(int fd, const void *buffer, size_t size)
{ (void)fd; (void)buffer; (void)size; errno = ENOSYS; return -1; }
int _read(int fd, void *buffer, size_t size)
{ (void)fd; (void)buffer; (void)size; errno = ENOSYS; return -1; }
int _close(int fd) { (void)fd; errno = EBADF; return -1; }
int _fstat(int fd, struct stat *st) { (void)fd; st->st_mode = S_IFCHR; return 0; }
int _isatty(int fd) { (void)fd; return 1; }
off_t _lseek(int fd, off_t off, int whence) { (void)fd; (void)off; (void)whence; errno = ESPIPE; return -1; }
int _getpid(void) { return 1; }
int _kill(int pid, int sig) { (void)pid; (void)sig; errno = EINVAL; return -1; }
void _exit(int status) { (void)status; Default_Handler(); for (;;) {} }
