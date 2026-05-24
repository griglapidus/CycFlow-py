//
// bind_server.cpp — TcpServer + TcpServerManager (new in v0.4.0).
//
// TcpServer requires an external asio::io_context. The PyTcpServer wrapper
// owns its io_context and a dedicated I/O thread so Python users do not need
// to deal with ASIO directly. PyTcpServerManager exposes the C++ singleton
// (which has the same internal layout) under a stable Python-friendly facade.
//

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <asio.hpp>
#include <memory>
#include <thread>

#include "Core/RecBuffer.h"
#include "Tcp/TcpServer.h"
#include "Tcp/TcpServerManager.h"

namespace py = pybind11;

namespace cyclibpy {

// ---------------------------------------------------------------------------
// PyTcpServer — owns its own io_context, work_guard, and I/O thread.
// ---------------------------------------------------------------------------
class PyTcpServer {
public:
    PyTcpServer(uint16_t port)
        : m_io(),
          m_work(asio::make_work_guard(m_io)),
          m_server(std::make_unique<cyc::TcpServer>(m_io, port)),
          m_running(false) {}

    ~PyTcpServer() { stop(); }

    void register_buffer(const std::string& name,
                         std::shared_ptr<cyc::RecBuffer> buffer,
                         std::size_t batch_size) {
        m_server->registerBuffer(name, std::move(buffer), batch_size);
    }

    void unregister_buffer(const std::string& name) {
        m_server->unregisterBuffer(name);
    }

    void start() {
        if (m_running.exchange(true)) return;
        m_server->start();
        m_io_thread = std::thread([this]() {
            try { m_io.run(); } catch (...) { /* swallow */ }
        });
    }

    void stop() {
        if (!m_running.exchange(false)) return;
        m_work.reset();
        m_io.stop();
        if (m_io_thread.joinable()) m_io_thread.join();
    }

    bool is_running() const { return m_running.load(); }

private:
    asio::io_context                                m_io;
    asio::executor_work_guard<asio::io_context::executor_type> m_work;
    std::unique_ptr<cyc::TcpServer>                 m_server;
    std::thread                                      m_io_thread;
    std::atomic<bool>                                m_running;
};


// ---------------------------------------------------------------------------
// PyTcpServerManager — thin facade around the C++ singleton.
//
// Holds no per-instance state; every call dispatches to
// cyc::TcpServerManager::instance(). Python only ever sees a single
// `TcpServerManager` instance returned by `TcpServerManager.instance()`.
// ---------------------------------------------------------------------------
class PyTcpServerManager {
public:
    static PyTcpServerManager& instance() {
        static PyTcpServerManager inst;
        return inst;
    }

    void start(uint16_t port) {
        cyc::TcpServerManager::instance().start(port);
    }

    void stop() {
        cyc::TcpServerManager::instance().stop();
    }

    bool is_running() const {
        return cyc::TcpServerManager::instance().isRunning();
    }

    void register_buffer(const std::string& name,
                         std::shared_ptr<cyc::RecBuffer> buffer,
                         std::size_t batch_size) {
        auto* srv = cyc::TcpServerManager::instance().server();
        if (!srv) throw std::runtime_error(
            "TcpServerManager: server is not running — call start() first.");
        srv->registerBuffer(name, std::move(buffer), batch_size);
    }

    void unregister_buffer(const std::string& name) {
        auto* srv = cyc::TcpServerManager::instance().server();
        if (!srv) throw std::runtime_error(
            "TcpServerManager: server is not running — call start() first.");
        srv->unregisterBuffer(name);
    }

private:
    PyTcpServerManager() = default;
};


void bind_server(py::module_& m) {
    py::class_<PyTcpServer>(m, "TcpServer",
        "CycFlow TCP server. Owns its own ASIO io_context and worker thread; "
        "use register_buffer() to publish buffers and call start().")
        .def(py::init<uint16_t>(), py::arg("port"))

        .def("register_buffer",
             &PyTcpServer::register_buffer,
             py::arg("name"), py::arg("buffer"), py::arg("batch_size") = 1000,
             py::keep_alive<1, 3>(),
             "Expose a RecBuffer under `name` to connecting clients.")

        .def("unregister_buffer",
             &PyTcpServer::unregister_buffer,
             py::arg("name"),
             py::call_guard<py::gil_scoped_release>(),
             "Remove the buffer and synchronously close every active "
             "client session serving it.")

        .def("start", &PyTcpServer::start,
             py::call_guard<py::gil_scoped_release>(),
             "Start accepting clients on a background I/O thread.")

        .def("stop",  &PyTcpServer::stop,
             py::call_guard<py::gil_scoped_release>(),
             "Stop the server and join the I/O thread.")

        .def("is_running", &PyTcpServer::is_running)

        .def("__enter__", [](py::object self) {
            self.attr("start")();
            return self;
        })
        .def("__exit__",  [](PyTcpServer& s,
                             py::object, py::object, py::object) {
            s.stop();
            return false;
        });

    // ----- TcpServerManager singleton ----------------------------------------
    // py::nodelete keeps Python from destroying the singleton on GC.
    py::class_<PyTcpServerManager,
               std::unique_ptr<PyTcpServerManager, py::nodelete>>(
        m, "TcpServerManager",
        "Global singleton that owns one TcpServer + io_context. "
        "Use TcpServerManager.instance() to get the singleton.")
        .def_static("instance", &PyTcpServerManager::instance,
                    py::return_value_policy::reference)
        .def("start", &PyTcpServerManager::start, py::arg("port") = 5000,
             py::call_guard<py::gil_scoped_release>(),
             "Start the singleton server. Idempotent.")
        .def("stop", &PyTcpServerManager::stop,
             py::call_guard<py::gil_scoped_release>(),
             "Stop the server. Idempotent. start() may be called again "
             "afterwards, possibly with a different port.")
        .def("is_running", &PyTcpServerManager::is_running)
        .def("register_buffer", &PyTcpServerManager::register_buffer,
             py::arg("name"), py::arg("buffer"), py::arg("batch_size") = 1000,
             py::keep_alive<1, 3>(),
             "Publish a RecBuffer. Raises if the server is not running.")
        .def("unregister_buffer", &PyTcpServerManager::unregister_buffer,
             py::arg("name"),
             py::call_guard<py::gil_scoped_release>(),
             "Remove a previously published buffer and close active sessions.");
}

} // namespace cyclibpy
