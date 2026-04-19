//
// bind_server.cpp — TcpServer.
//
// TcpServer requires an external asio::io_context. To make it usable from
// Python without leaking ASIO into the public API, we provide a thin wrapper
// (PyTcpServer) that owns its io_context and a dedicated I/O thread.
//

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <asio.hpp>
#include <memory>
#include <thread>

#include "Core/RecBuffer.h"
#include "Tcp/TcpServer.h"

namespace py = pybind11;

namespace cyclibpy {

// Wrapper that owns an io_context, a work_guard, and an I/O thread.
// This matches the pattern from the CycFlow README server example.
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

    void start() {
        if (m_running.exchange(true)) return;  // idempotent
        m_server->start();
        // Run io_context on a dedicated thread so Python keeps control.
        m_io_thread = std::thread([this]() {
            try { m_io.run(); } catch (...) { /* swallow: logged elsewhere */ }
        });
    }

    void stop() {
        if (!m_running.exchange(false)) return;
        m_work.reset();    // let run() return once all work is done
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


void bind_server(py::module_& m) {
    py::class_<PyTcpServer>(m, "TcpServer",
        "CycFlow TCP server. Owns its own ASIO io_context and worker thread; "
        "use register_buffer() to publish buffers and call start().")
        .def(py::init<uint16_t>(), py::arg("port"))

        .def("register_buffer",
             &PyTcpServer::register_buffer,
             py::arg("name"), py::arg("buffer"), py::arg("batch_size") = 1000,
             // keep the RecBuffer alive as long as the server holds it
             py::keep_alive<1, 3>(),
             "Expose a RecBuffer under `name` to connecting clients.")

        .def("start", &PyTcpServer::start,
             py::call_guard<py::gil_scoped_release>(),
             "Start accepting clients on a background I/O thread.")

        .def("stop",  &PyTcpServer::stop,
             py::call_guard<py::gil_scoped_release>(),
             "Stop the server and join the I/O thread.")

        .def("is_running", &PyTcpServer::is_running)

        // Context-manager for convenient cleanup.
        .def("__enter__", [](py::object self) {
            self.attr("start")();
            return self;
        })
        .def("__exit__",  [](PyTcpServer& s,
                             py::object, py::object, py::object) {
            s.stop();
            return false;
        });
}

} // namespace cyclibpy
