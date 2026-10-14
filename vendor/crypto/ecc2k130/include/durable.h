// POSIX persistence helpers. A successful flush means data reached the OS's
// durable-storage boundary, not merely the C stdio buffer.
#pragma once
#include <cstdio>
#include <string>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

inline bool durableFlush(FILE *f) {
    return fflush(f) == 0 && fsync(fileno(f)) == 0;
}

inline bool syncParent(const std::string &path) {
    const size_t slash = path.find_last_of('/');
    const std::string parent = slash == std::string::npos ? "." :
                               (slash == 0 ? "/" : path.substr(0, slash));
    const int fd = open(parent.c_str(), O_RDONLY | O_DIRECTORY);
    if (fd < 0) return false;
    const bool ok = fsync(fd) == 0;
    close(fd);
    return ok;
}

struct CorpusOutput {
    FILE *file = nullptr;
    // Size at open, measured under the lock.  A caller that frames its records
    // with a leading header needs this to tell a fresh corpus from an existing
    // one; the append handle's own ftell cannot answer that.
    long long bytes = 0;
    ~CorpusOutput() { if (file) fclose(file); }
    bool openFile(const std::string &path, size_t recordBytes, size_t headerBytes = 0) {
        // Do not block on a FIFO/device or read an endless device as a corpus.
        struct stat st;
        if (lstat(path.c_str(), &st) == 0 && !S_ISREG(st.st_mode)) return false;
        file = fopen(path.c_str(), "ab");
        if (!file) return false;
        if (flock(fileno(file), LOCK_EX | LOCK_NB) != 0 ||
            fstat(fileno(file), &st) != 0 || !S_ISREG(st.st_mode)) return false;
        bytes = (long long)st.st_size;
        // An empty corpus is whole whatever its framing; a non-empty one is a
        // header plus a whole number of records.
        if (bytes != 0 && ((unsigned long long)bytes < headerBytes ||
                           ((unsigned long long)bytes - headerBytes) % recordBytes != 0))
            return false;
        return syncParent(path);
    }
    bool closeFile() {
        if (!file) return true;
        const bool flushed = durableFlush(file);
        const bool closed = fclose(file) == 0;
        file = nullptr;
        return flushed && closed;
    }
};
